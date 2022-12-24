import toml
from mako.template import Template
from mako import exceptions
import platform
from collections import defaultdict, OrderedDict
import os.path
from vectortween.NumberAnimation import NumberAnimation
from vectortween.PointAnimation import PointAnimation
from vectortween.SequentialAnimation import SequentialAnimation
from vectortween.SumAnimation import SumAnimation
from vectortween.Mapping import Mapping
import string
import ast
import PIL.Image
import subprocess
import io
import numpy as np
import moviepy
from moviepy.editor import CompositeVideoClip
from pathlib import Path


def to_numpy(image, width, height):
    '''  Converts a QImage into numpy format  '''
    arr = np.array(image).reshape(height, width, 4)  # Copies the data
    return arr[:, :, :3]  # remove alpha channel


class CaptionGenerator(object):
    def __init__(self, output_file):
        self.template_folder = str(Path(__file__).absolute().parent.joinpath("templates"))
        self.output_file = output_file
        self.output_folder = str(Path(output_file).parent)
        guess = defaultdict(lambda key: "")
        guess['Linux'] = '/usr/bin/inkscape'
        guess['Windows'] = r'c:\Program Files\Inkscape\Inkscape.exe'
        guess['Darwin'] = r'/Applications/Inkscape.app/Contents/MacOS/inkscape'  # ???
        self.inkscape = guess[platform.system()]
        self.frame_maker = None

    def duration(self):
        return self._eval_expr(self._replace_globals('${Global.duration}'))

    def video_format(self):
        return self.spec['Global']['format'].lower()

    def fps(self):
        return self._eval_expr(self._replace_globals('${Global.fps}'))

    def initialize_from_file(self, filename: str) -> bool:
        try:
            with open(filename, "r") as f:
                contents = f.read()
            return self.initialize_from_string(contents)
        except IOError as e:
            print('Error opening file ' + str(filename) + '\n' + str(e))
            return False

    def initialize_from_string(self, contents: str) -> bool:
        self.spec = toml.loads(contents)
        if not self.validate_spec():
            print("Errors in specification found.")
            return False
        if not self._build_animations():
            print("Errors in animation specification found.")
            return False
        self.frame_maker = self._build_make_frame(25)
        return True

    def _check_section_present(self, section: str, subspec: dict) -> bool:
        if not section in subspec:
            print(f"Error in specification: must have a [{section}] section.")
            return False
        return True

    def _check_key_present(self, key: str, sectionname: str, subspec: dict) -> bool:
        if not key in subspec:
            print(f"Error in specification: must have a key {key} in [{sectionname}]")
            return False
        return True

    def _check_all_keys_present(self, keys: list[str], sectionname: str, subspec: dict) -> bool:
        all_ok = True
        for key in keys:
            all_ok = self._check_key_present(key, sectionname, subspec) and all_ok
        return all_ok

    def _check_all_leaves_are_strings(self, sectionname, spec) -> bool:
        for key in spec:
            if type(spec[key]) is dict:
                new_sectionname = sectionname + "." + key if sectionname else key
                self._check_all_leaves_are_strings(new_sectionname, spec[key])
            elif type(spec[key]) != str:
                print(
                    f"Error: all fields in specification must be strings. Found an entry {sectionname}.{key} with type {type(spec[key])} instead.")
                return False
        return True

    def _check_animation_types(self, spec):
        if 'Position' in spec:
            for pos_anim in spec['Position']:
                anim_spec = spec['Position'][pos_anim]
                if 'type' not in anim_spec:
                    print(f"Error! Animation Animations.Position.{pos_anim} does not specify a type.")
                    return False
                the_type = spec['Position'][pos_anim]['type']
                allowed_types = ['PointAnimation', 'SumAnimation', 'SequentialAnimation']
                if the_type not in allowed_types:
                    print(
                        f"Error: Position animations must be one of {allowed_types}.\nAnimations.Position.{pos_anim} specifies type '{the_type}' instead.")
                    return False
        if 'Style' in spec:
            for style_anim in spec['Style']:
                anim_spec = spec['Style'][style_anim]
                if 'type' not in anim_spec:
                    print(f"Error! Animation Animations.Style.{style_anim} does not specify a type.")
                    return False
                the_type = spec['Style'][style_anim]['type']
                allowed_animations = ['NumberAnimation', 'SumAnimation', 'SequentialAnimation']
                if the_type not in allowed_animations:
                    print(
                        f"Error: Style animations must be one of {allowed_animations}.\nAnimations.Style.{style_anim} specifies type '{the_type}' instead.")
                    return False
        return True

    def _check_styles_properties(self, stylesspec):
        for key in stylesspec:
            if "StyleProperties" not in stylesspec[key]:
                print(f"Error! In section Styles.{key} no StyleProperties section is found.")
                return False
        return True

    def _supported_tween_methods(self):
        return ['linear',
                'easeInQuad', 'easeOutQuad', 'easeInOutQuad',
                'easeInCubic', 'easeOutCubic', 'easeInOutCubic',
                'easeInQuart', 'easeOutQuart', 'easeInOutQuart',
                'easeInQuint', 'easeOutQuint', 'easeInOutQuint',
                'easeInSine', 'easeOutSine', 'easeInOutSine',
                'easeInExpo', 'easeOutExpo', 'easeInOutExpo',
                'easeInCirc', 'easeOutCirc', 'easeInOutCirc',
                'easeInBounce', 'easeOutBounce', 'easeInOutBounce']

    def _check_valid_tween(self, tween):
        supported_tweens = self._supported_tween_methods()
        if tween not in supported_tweens:
            return False
        return True

    def _check_styles(self, spec):
        if 'Caption' in self.spec:
            for caption in self.spec['Caption']:
                if not 'Segments' in self.spec['Caption'][caption]:
                    print(f"Error! No Caption.{caption}.Segments section defined!")
                    return False
                for segment in self.spec['Caption'][caption]['Segments']:
                    if 'text' not in self.spec['Caption'][caption]['Segments'][segment]:
                        print(f"Error. No text property defined in Caption.{caption}.Segments.{segment}")
                        return False
                    txt = self.spec['Caption'][caption]['Segments'][segment]['text']
                    if 'style' not in self.spec['Caption'][caption]['Segments'][segment]:
                        print(f"Error. No style property defined in Caption.{caption}.Segments.{segment}")
                        return False
                    if "${" not in self.spec['Caption'][caption]['Segments'][segment]['style']:
                        print(
                            f"Error. Property style in section Caption.{caption}.Segments.{segment} must refer to one of the styles from the Styles section!")
                        return False
        return True

    def validate_spec(self) -> bool:
        if not type(self.spec) is dict:
            print("Error in specification: expected a valid toml file.")
            return False

        all_ok = True
        all_ok = self._check_section_present('Global', self.spec) and all_ok
        all_ok = self._check_all_keys_present(['W', 'H', 'duration'], 'Global', self.spec['Global']) and all_ok
        all_ok = self._check_section_present('Animations', self.spec) and all_ok
        all_ok = self._check_animation_types(self.spec['Animations']) and all_ok
        all_ok = self._check_section_present('Styles', self.spec) and all_ok
        all_ok = self._check_styles_properties(self.spec['Styles']) and all_ok
        all_ok = self._check_section_present('Caption', self.spec) and all_ok
        all_ok = self._check_all_leaves_are_strings('', self.spec) and all_ok
        all_ok = self._check_styles(self.spec) and all_ok

        return all_ok

    def _replace_placeholders(self, the_string, placeholders):
        for p in placeholders:
            the_string = the_string.replace(str(p), str(placeholders[p]))
        return the_string

    def _replace_globals(self, the_string):
        placeholders = {}
        for key in self.spec['Global']:
            placeholders[f"${{Global.{key}}}"] = self.spec['Global'][key]
        return self._replace_placeholders(the_string, placeholders)

    def _eval_expr(self, expr):
        try:
            tree = ast.parse(expr, mode='eval')
        except SyntaxError:
            return expr  # not a Python expression
        if not all(isinstance(node, (ast.Expression, ast.List, ast.Constant, ast.Load,
                                     ast.UnaryOp, ast.unaryop,
                                     ast.BinOp, ast.operator,
                                     ast.Num)) for node in ast.walk(tree)):
            return expr  # not a mathematical expression (numbers and operators)
        result = eval(compile(tree, filename='', mode='eval'))
        return result

    def _listel_from_str(self, string):
        return string.replace(" ", "").replace("\t", "").strip()[1:-1].split(",")

    def _collect_animations(self, kind, basic_type_name, basic_type_class):
        if basic_type_name == "PointAnimation":
            default_begin_end = [0, 0]
            allowed_begin_end_types = (list,)
        else:
            default_begin_end = 0
            allowed_begin_end_types = (int, float)

        if kind in self.spec['Animations']:
            for anim_instance in self.spec['Animations'][kind]:
                tp = self.spec['Animations'][kind][anim_instance]
                the_type = tp['type']
                if the_type == basic_type_name:
                    if 'begin' not in tp:
                        print(f"Warning: no 'begin' specified in Animations.{kind}.{anim_instance}. Using 0 instead.")
                    begin_str = self._replace_globals(tp['begin']) if 'begin' in tp else default_begin_end
                    begin_numeric = self._eval_expr(begin_str)
                    if not isinstance(begin_numeric, allowed_begin_end_types):
                        print(
                            f"Invalid expression in Animations.{kind}.{anim_instance}.begin. Expected to find a {type(default_begin_end)}. Found {begin_numeric} instead.")
                        return False
                    if 'end' not in tp:
                        print(
                            f"Warning: no 'end' specified in Animations.{kind}.{anim_instance}. Using {default_begin_end} instead.")
                    end_str = self._replace_globals(tp['end']) if 'end' in tp else default_begin_end
                    end_numeric = self._eval_expr(end_str)
                    if not isinstance(end_numeric, allowed_begin_end_types):
                        print(
                            f"Invalid expression in Animations.{kind}.{anim_instance}.end. Expected to find a {type(default_begin_end)}. Found {end_numeric} instead.")
                        return False
                    tween = tp['tween'] if 'tween' in tp else 'linear'
                    if not self._check_valid_tween(tween):
                        print(
                            f"Invalid tween method in Animations.{kind}.{anim_instance}.tween. Expected one of {self._supported_tween_methods()}")
                        return False
                    self.animations[kind][anim_instance] = basic_type_class(begin_numeric, end_numeric, [tween])

            # then collect all sequential and sum animations
            for anim_instance in self.spec['Animations'][kind]:
                tp = self.spec['Animations'][kind][anim_instance]
                the_type = tp['type']
                if the_type == "SequentialAnimation":
                    elements_str = self._listel_from_str(tp['elements'])
                    elements = []
                    for el in elements_str:
                        stripped_el = el[len(f"${{Animations.{kind}."):-1]
                        if stripped_el not in self.animations[kind]:
                            print(
                                f"Error: sequential animation Animations.{kind}.{anim_instance} uses another animation named {el[2:-1]} which is not defined yet.")
                            return False
                        elements.append(self.animations[kind][stripped_el])

                    timeweights_str = self._replace_globals(tp['time_weights']) if 'time_weights' in tp else None
                    timeweights = self._eval_expr(timeweights_str) if timeweights_str else None
                    if timeweights is not None and len(timeweights) != len(elements_str):
                        print(
                            f"Timeweights must have same length as elements in Animations.{kind}.{anim_instance}.time_weights. Currently len(elements) = {len(elements_str)}, but len(time_weights) = {len(timeweights)}")
                        return False
                    repeats = self._replace_globals(tp['repeats']) if 'repeats' in tp else '1'
                    repeats = self._eval_expr(repeats)
                    tween = tp['tween'] if 'tween' in tp else 'linear'
                    if not self._check_valid_tween(tween):
                        print(
                            f"Invalid tween method in Animations.{kind}.{anim_instance}.tween. Expected one of {self._supported_tween_methods()}")
                        return False
                    self.animations[kind][anim_instance] = SequentialAnimation(
                        list_of_animations=elements,
                        timeweight=timeweights,
                        repeats=repeats,
                        tween=[tween])
                elif the_type == 'SumAnimation':
                    elements_str = self._listel_from_str(tp['elements'])
                    elements = []
                    for el in elements_str:
                        stripped_el = el[len(f"${{Animations.{kind}."):-1]
                        if stripped_el not in self.animations[kind]:
                            print(
                                f"Error: sequential animation Animations.{kind}.{anim_instance} uses another animation named {el[2:-1]} which is not defined yet.")
                            return False
                        elements.append(self.animations[kind][stripped_el])
                    self.animations[kind][anim_instance] = SumAnimation(list_of_animations=elements)
        return True

    def _collect_position_animations(self):
        return self._collect_animations('Position', 'PointAnimation', PointAnimation)

    def _collect_captionsvgattribute_animations(self):
        return self._collect_animations('CaptionSvgAttribute', 'NumberAnimation', NumberAnimation)

    def _collect_segmentsvgattribute_animations(self):
        return self._collect_animations('SegmentSvgAttribute', 'NumberAnimation', NumberAnimation)

    def _collect_style_animations(self):
        return self._collect_animations('Style', 'NumberAnimation', NumberAnimation)

    def _collect_textprovider_animations(self):
        return self._collect_animations('TextProvider', 'NumberAnimation', NumberAnimation)

    def _build_animations(self):
        self.animations = {}

        self.animations['Position'] = {}
        if not self._collect_position_animations():
            return False

        self.animations["CaptionSvgAttribute"] = {}
        if not self._collect_captionsvgattribute_animations():
            return False

        self.animations['SegmentSvgAttribute'] = {}
        if not self._collect_segmentsvgattribute_animations():
            return False

        self.animations['Style'] = {}
        if not self._collect_style_animations():
            return False

        self.animations['TextProvider'] = {}
        if not self._collect_textprovider_animations():
            return False

        return True

    def _get_text_per_segment_for_line(self, text_per_line_per_segment, line, animated_value):
        text_per_segment = text_per_line_per_segment[line]
        total_text = ""
        for s in text_per_segment:
            total_text += text_per_segment[s]
        len_total_text = len(total_text)
        text_values = OrderedDict()

        if animated_value is None:
            for s in text_per_segment:
                text_values[f"text_{line}_{s}"] = ""
        else:
            if animated_value >= 0:
                reverse = False
                end_index = Mapping.linlin(animated_value, 0, 100, 0, len_total_text - 1, clip=True)
            else:
                reverse = True
                end_index = Mapping.linlin(-animated_value, 0, 100, 0, len_total_text - 1, clip=True)

            if not reverse:
                cur_index = 0
                for s in text_per_segment:
                    partial_s = ""
                    for character in text_per_segment[s]:
                        if cur_index <= end_index:
                            partial_s += character
                            cur_index += 1
                        else:
                            cur_index += 1
                    text_values[f"text_{line}_{s}"] = partial_s
            else:
                cur_index = 0
                for s in reversed(text_per_segment):
                    partial_s = ""
                    for character in reversed(text_per_segment[s]):
                        if cur_index <= end_index:
                            partial_s = character + partial_s
                            cur_index += 1
                        else:
                            cur_index += 1
                    text_values[f"text_{line}_{s}"] = partial_s
                    text_values.move_to_end(f"text_{line}_{s}",
                                            last=False)  # reverse order since we iterated in reverse
        return text_values

    def _make_svg_string(self) -> bool:
        try:
            svg_text_template = Template(filename=os.path.join(self.template_folder, "doc.svgtemplate"),
                                         module_directory=os.path.join(self.template_folder, "modules"))
            return True, svg_text_template.render(spec=self.spec)
        except:
            print(exceptions.text_error_template().render())
        return False, ""

    def _parse_animation_times(self, fps, line, kind):
        birth_frame = 0
        start_frame = 0
        stop_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        death_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        if kind in self.spec['Caption'][line]:
            time_section = self.spec['Caption'][line][kind]
            if 'birth_time' in time_section:
                birth_frame = self._eval_expr(self._replace_globals(time_section['birth_time'])) * fps
            else:
                print(
                    f"Warning: no birth_time specified in Caption.{line}.{kind}. Using {birth_frame}.")
            if 'begin_time' in time_section:
                start_frame = self._eval_expr(self._replace_globals(time_section['begin_time'])) * fps
            else:
                print(
                    f"Warning: no start_time specified in Caption.{line}.{kind}. Using {start_frame}.")
            if 'end_time' in time_section:
                stop_frame = self._eval_expr(self._replace_globals(time_section['end_time'])) * fps
            else:
                print(
                    f"Warning: no stop_time specified in Caption.{line}.{kind}. Using {stop_frame}.")
            if 'death_time' in time_section:
                death_frame = self._eval_expr(self._replace_globals(time_section['death_time'])) * fps
            else:
                print(
                    f"Warning: no death_time specified in Caption.{line}.{kind}. Using {death_frame}.")
        return birth_frame, start_frame, stop_frame, death_frame

    def _parse_style_animation_times(self, fps, style_name, kind):
        birth_frame = 0
        start_frame = 0
        stop_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        death_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        if kind in self.spec['Styles'][style_name]['StyleAnimation']:
            time_section = self.spec['Styles'][style_name]['StyleAnimation'][kind]
            if 'birth_time' in time_section:
                birth_frame = self._eval_expr(self._replace_globals(time_section['birth_time'])) * fps
            else:
                print(
                    f"Warning: no birth_time specified in Caption.{style_name}.StyleAnimation.{kind}. Using {birth_frame}.")
            if 'begin_time' in time_section:
                start_frame = self._eval_expr(self._replace_globals(time_section['begin_time'])) * fps
            else:
                print(
                    f"Warning: no start_time specified in Caption.{style_name}.StyleAnimation.{kind}. Using {start_frame}.")
            if 'end_time' in time_section:
                stop_frame = self._eval_expr(self._replace_globals(time_section['end_time'])) * fps
            else:
                print(
                    f"Warning: no stop_time specified in Caption.{style_name}.StyleAnimation.{kind}. Using {stop_frame}.")
            if 'death_time' in time_section:
                death_frame = self._eval_expr(self._replace_globals(time_section['death_time'])) * fps
            else:
                print(
                    f"Warning: no death_time specified in Caption.{style_name}.StyleAnimation.{kind}. Using {death_frame}.")
        return birth_frame, start_frame, stop_frame, death_frame

    def _parse_captionsvgattribute_animation_times(self, fps, attrib_anim_name):
        birth_frame = 0
        start_frame = 0
        stop_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        death_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        if not attrib_anim_name in self.spec['Animations']['CaptionSvgAttribute']:
            print(
                f"Error! didn't find a section Animations.CaptionSvgAttribute.{attrib_anim_name}. Using birth_frame = {birth_frame}, start_frame = {start_frame}, stop_frame = {stop_frame}, death_frame = {death_frame}.")
        else:
            if 'CaptionSvgAttributeAnimation' not in self.spec['Animations']['CaptionSvgAttribute'][attrib_anim_name]:
                print(
                    f"Warning: no Animations.CaptionSvgAttribute.{attrib_anim_name}.CaptionSvgAttributeAnimation section found. Using birth_frame = {birth_frame}, start_frame = {start_frame}, stop_frame = {stop_frame}, death_frame = {death_frame}.")
            else:
                time_section = self.spec['Animations']['CaptionSvgAttribute'][attrib_anim_name][
                    'CaptionSvgAttributeAnimation']
                if 'birth_time' in time_section:
                    birth_frame = self._eval_expr(self._replace_globals(time_section['birth_time'])) * fps
                else:
                    print(
                        f"Warning: no birth_time specified in Animations.CaptionSvgAttribute.{attrib_anim_name}. Using {birth_frame}.")
                if 'begin_time' in time_section:
                    start_frame = self._eval_expr(self._replace_globals(time_section['begin_time'])) * fps
                else:
                    print(
                        f"Warning: no start_time specified in Animations.CaptionSvgAttribute.{attrib_anim_name}. Using {start_frame}.")
                if 'end_time' in time_section:
                    stop_frame = self._eval_expr(self._replace_globals(time_section['end_time'])) * fps
                else:
                    print(
                        f"Warning: no stop_time specified in Animations.CaptionSvgAttribute.{attrib_anim_name}. Using {stop_frame}.")
                if 'death_time' in time_section:
                    death_frame = self._eval_expr(self._replace_globals(time_section['death_time'])) * fps
                else:
                    print(
                        f"Warning: no death_time specified in Animations.CaptionSvgAttribute.{attrib_anim_name}. Using {death_frame}.")
        return birth_frame, start_frame, stop_frame, death_frame

    def _parse_segmentsvgattribute_animation_times(self, fps, attrib_anim_name):
        birth_frame = 0
        start_frame = 0
        stop_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        death_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        if not attrib_anim_name in self.spec['Animations']['SegmentSvgAttribute']:
            print(
                f"Error! didn't find a section Animations.SegmentSvgAttribute.{attrib_anim_name}. Using birth_frame = {birth_frame}, start_frame = {start_frame}, stop_frame = {stop_frame}, death_frame = {death_frame}.")
        else:
            if 'SegmentSvgAttributeAnimation' not in self.spec['Animations']['SegmentSvgAttribute'][attrib_anim_name]:
                print(
                    f"Warning: no Animations.SegmentSvgAttribute.{attrib_anim_name}.SegmentSvgAttributeAnimation section found. Using birth_frame = {birth_frame}, start_frame = {start_frame}, stop_frame = {stop_frame}, death_frame = {death_frame}.")
            else:
                time_section = self.spec['Animations']['SegmentSvgAttribute'][attrib_anim_name][
                    'SegmentSvgAttributeAnimation']
                if 'birth_time' in time_section:
                    birth_frame = self._eval_expr(self._replace_globals(time_section['birth_time'])) * fps
                else:
                    print(
                        f"Warning: no birth_time specified in Animations.SegmentSvgAttribute.{attrib_anim_name}. Using {birth_frame}.")
                if 'begin_time' in time_section:
                    start_frame = self._eval_expr(self._replace_globals(time_section['begin_time'])) * fps
                else:
                    print(
                        f"Warning: no start_time specified in Animations.SegmentSvgAttribute.{attrib_anim_name}. Using {start_frame}.")
                if 'end_time' in time_section:
                    stop_frame = self._eval_expr(self._replace_globals(time_section['end_time'])) * fps
                else:
                    print(
                        f"Warning: no stop_time specified in Animations.SegmentSvgAttribute.{attrib_anim_name}. Using {stop_frame}.")
                if 'death_time' in time_section:
                    death_frame = self._eval_expr(self._replace_globals(time_section['death_time'])) * fps
                else:
                    print(
                        f"Warning: no death_time specified in Animations.SegmentSvgAttribute.{attrib_anim_name}. Using {death_frame}.")
        return birth_frame, start_frame, stop_frame, death_frame

    def _resolve_textprovider_animations(self, fps, current_frame, line, svg):
        text_per_line_per_segment = defaultdict(lambda: defaultdict(lambda: ""))
        for segment in self.spec['Caption'][line]['Segments']:
            text_per_line_per_segment[line][segment] = self.spec['Caption'][line]['Segments'][segment]['text']
        if 'TextProvider' not in self.spec['Caption'][line]:
            animated_value = 100
        else:
            if 'style' not in self.spec['Caption'][line]['TextProvider']:
                print(f"Error! In Caption.{line}.TextProvider, no style is defined.")
                return False
            text_provider_style = self.spec['Caption'][line]['TextProvider']['style']
            if "${" not in text_provider_style:
                print(
                    f"Error! Caption.{line}.TextProvider.style, must point to a textprovider from Animations.TextProvider");
                return False
            short_provider_name = text_provider_style[len("${Animations.TextProvider."):-1]
            if short_provider_name not in self.animations['TextProvider']:
                print(
                    f"Error Caption.{line}.TextProvider.style uses a style {short_provider_name} which is not defined in the Animation.TextProvider section.")
                return False
            animation = self.animations['TextProvider'][short_provider_name]
            birth_frame, start_frame, stop_frame, death_frame = self._parse_animation_times(fps, line,
                                                                                            'TextProviderAnimation')
            animated_value = animation.make_frame(current_frame,
                                                  birth_frame,
                                                  start_frame,
                                                  stop_frame,
                                                  death_frame)

        resolved_text_values = self._get_text_per_segment_for_line(text_per_line_per_segment, line,
                                                                   animated_value)
        svg = string.Template(svg).safe_substitute(resolved_text_values)
        return svg

    def _resolve_captionsvgattribute_animations(self, fps, current_frame, line, svg):
        if 'CaptionSvgAttribute' in self.spec['Caption'][line]:
            for key in self.spec['Caption'][line]['CaptionSvgAttribute']:
                caption_property_keyval = self.spec['Caption'][line]['CaptionSvgAttribute'][key]
                if "${" in caption_property_keyval:
                    short_provider_name = caption_property_keyval[len("${Animations.CaptionSvgAttribute."):-1]
                    if short_provider_name not in self.animations['CaptionSvgAttribute']:
                        print(
                            f"Error: Caption.{line}.CaptionSvgAttribute specifies an animation {short_provider_name} which is not defined in the Animations.CaptionSvgAttribute section.")
                        return False
                    animation = self.animations['CaptionSvgAttribute'][short_provider_name]
                    birth_frame, start_frame, stop_frame, death_frame = self._parse_captionsvgattribute_animation_times( \
                        fps,
                        short_provider_name)
                    animated_value = animation.make_frame(current_frame,
                                                          birth_frame,
                                                          start_frame,
                                                          stop_frame,
                                                          death_frame)
                    resolved_style_values = {
                        "${Animations.CaptionSvgAttribute." + short_provider_name + "_for_line_" + line + "}": animated_value}
                    svg = self._replace_placeholders(svg, resolved_style_values)
        return svg

    def _resolve_segmentsvgattribute_animations(self, fps, current_frame, line, segment, svg):
        if 'SegmentSvgAttribute' in self.spec['Caption'][line]['Segments'][segment]:
            for key in self.spec['Caption'][line]['Segments'][segment]['SegmentSvgAttribute']:
                segment_property_keyval = self.spec['Caption'][line]['Segments'][segment]['SegmentSvgAttribute'][key]
                if "${" in segment_property_keyval:
                    short_provider_name = segment_property_keyval[len("${Animations.SegmentSvgAttribute."):-1]
                    if short_provider_name not in self.animations['SegmentSvgAttribute']:
                        print(
                            f"Error: Caption.{line}.Segments.{segment}.SegmentSvgAttribute specifies an animation which is not defined in the Animations.SegmentSvgAttribute section.")
                        return False
                animation = self.animations['SegmentSvgAttribute'][short_provider_name]
                birth_frame, start_frame, stop_frame, death_frame = self._parse_segmentsvgattribute_animation_times(fps,
                                                                                                                    short_provider_name)
                animated_value = animation.make_frame(current_frame,
                                                      birth_frame,
                                                      start_frame,
                                                      stop_frame,
                                                      death_frame)
                resolved_style_values = {
                    "${Animations.SegmentSvgAttribute." + short_provider_name + "_for_line_" + line + "_for_segment_" + segment + "}": animated_value
                }
                svg = self._replace_placeholders(svg, resolved_style_values)
        return svg

    def _resolve_position_animations(self, fps, current_frame, line, svg):
        current_pos = [0, 0]
        if 'offset' not in self.spec['Caption'][line]:
            offset = [0, 0]
        elif "${" in self.spec['Caption'][line]['x_offset']:
            offset = [0, 0]  # todo animated offset
        else:
            offset = self._eval_expr(self._replace_globals(self.spec['Caption'][line]['offset']))

        if not 'pos' in self.spec['Caption'][line]:  # no position
            print(f"Warning: no position specified i caption Caption.{line}. Using {current_pos} instead.")
            resolved_values = {line + '_x': current_pos[0] + offset[0],
                               line + "_y": current_pos[1] + offset[1]}
            svg = string.Template(svg).safe_substitute(resolved_values)
        else:
            cap = self.spec['Caption'][line]
            if '${' in cap['pos']:  # animated position
                if not 'PositionAnimation' in cap:
                    print(
                        f"Error: animated position specified, but no PositionAnimation section present in Caption.{line}.")
                    return False
                birth_frame, start_frame, stop_frame, death_frame = self._parse_animation_times(fps, line,
                                                                                                'PositionAnimation')
                the_pos = self.spec['Caption'][line]['pos']
                if "[" in the_pos:
                    the_pos_el = self._listel_from_str(the_pos)
                else:
                    the_pos_el = [the_pos]
                current_pos = [0, 0]
                for index, animation in enumerate(the_pos_el):
                    if '${Animations.Position' in animation:
                        if len(the_pos_el) != 1:
                            print(
                                f"Error! Position animation in Caption.{line}.pos must be a single animation, or a list of 2 floats.")
                            return False

                        animation_short_name = the_pos_el[0][len("${Animations.Position."):-1]
                        animation_obj = self.animations['Position'][animation_short_name]
                        animation_value = animation_obj.make_frame(current_frame, birth_frame, start_frame,
                                                                   stop_frame, death_frame)
                        current_pos = list(animation_value)
                        if current_pos[0] is None:
                            current_pos[0] = 1e10  # move out of sight
                        if current_pos[1] is None:
                            current_pos[1] = 1e10  # move out of sight
                    else:
                        if len(the_pos_el) != 2:
                            print(
                                f"Error! Position animation in Caption.{line}.pos must be a single animation, or a list of 2 floats.")
                            return False
                        animation_value = float(animation)
                        current_pos[index] = animation_value
                resolved_values = {line + '_x': current_pos[0] + offset[0],
                                   line + "_y": current_pos[1] + offset[1]}
                svg = string.Template(svg).safe_substitute(resolved_values)
            else:  # fixed position
                current_pos = self._eval_expr(self._replace_globals(self.spec['Caption'][line]['pos']))
                resolved_values = {line + '_x': current_pos[0] + offset[0],
                                   line + "_y": current_pos[1] + offset[1]}
                svg = string.Template(svg).safe_substitute(resolved_values)
        return svg

    def _resolve_style_animations(self, fps, current_frame, line, svg):
        # resolve style animations
        for segment in self.spec['Caption'][line]['Segments']:
            birth_frame = 0
            death_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
            begin_frame = None
            end_frame = None
            if 'style' in self.spec['Caption'][line]['Segments'][segment]:
                style_name = self.spec['Caption'][line]['Segments'][segment]['style']
                style_name_short = style_name[len("${Styles."): -1]
                if style_name_short not in self.spec['Styles']:
                    print(
                        f"Error! section Caption.{line}.Segments.{segment} uses a style name {style_name} which has not been defined in the Styles section.")
                    return False
                style_definition = self.spec['Styles'][style_name_short]
                for property in style_definition['StyleProperties']:
                    prop_val = style_definition['StyleProperties'][property]
                    if "${" in prop_val:  # animated property

                        property_animation_short = prop_val[len("${Animations.Style."):-1]
                        birth_frame, begin_frame, end_frame, death_frame = self._parse_style_animation_times(fps,
                                                                                                             style_name_short,
                                                                                                             property_animation_short)

                        if property_animation_short in self.animations['Style']:
                            # animated style
                            animation = self.animations['Style'][property_animation_short]
                            animated_value = animation.make_frame(current_frame,
                                                                  birth_frame,
                                                                  begin_frame,
                                                                  end_frame,
                                                                  death_frame)
                            resolved_style_values = {
                                "${Animations.Style." + property_animation_short + "_for_style_" + style_name_short + "}": animated_value}
                            svg = self._replace_placeholders(svg, resolved_style_values)
                        else:
                            # unknown animated property
                            print(
                                f"section Caption.{line}.Segments.{segment}.StyleProperties.{property} uses an animation {style_definition['StyleProperties'][property]} that was not defined in the Animation.Styles section.")
                            return False
                    else:
                        # fixed property -> nothing to resolve
                        pass
            else:
                # todo: use a default style? for now, use no style (which is also a kind of default I guess)
                begin_frame = 0
                end_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        return svg

    def _build_make_frame(self, fps):
        def make_frame(t):
            current_frame = t * fps
            success, svg = self._make_svg_string()
            if not success:
                print(f"Error rendering svg template.")
                return False

            # resolve the different positions and position animations
            for line in self.spec['Caption']:
                svg = self._resolve_textprovider_animations(fps, current_frame, line, svg)
                if not svg:
                    return False

                svg = self._resolve_captionsvgattribute_animations(fps, current_frame, line, svg)
                if not svg:
                    return False

                for segment in self.spec['Caption'][line]['Segments']:
                    svg = self._resolve_segmentsvgattribute_animations(fps, current_frame, line, segment, svg)
                    if not svg:
                        return False

                svg = self._resolve_position_animations(fps, current_frame, line, svg)
                if not svg:
                    return False

                svg = self._resolve_style_animations(fps, current_frame, line, svg)
                if not svg:
                    return False

            if "${" in svg:
                print(
                    f"Error! Some animations or globals could not be resolved. Please check your specification for typos.")
                print(f"{svg}")
                return False

            W = self._eval_expr(self._replace_globals('${Global.W}'))
            H = self._eval_expr(self._replace_globals('${Global.H}'))
            background = self._replace_globals('${Global.background}')

            if self.video_format() == 'svg':
                frame = f"frame_{int(t * fps):08}.svg"
                destination = os.path.join(self.output_folder, frame)
                with open(destination, "w") as f:
                    f.write(svg)

            result = subprocess.run([self.inkscape,
                                     f'--export-background={background}',
                                     '--export-type=png',
                                     '--export-filename=-',
                                     f'--export-width={W}',
                                     f'--export-height={H}',
                                     '--pipe'],
                                    input=svg.encode(),
                                    capture_output=True)
            pngdata = result.stdout
            img = PIL.Image.open(io.BytesIO(pngdata), formats=["PNG"])
            return to_numpy(img, W, H)

        return make_frame

    def make_txt_clip(self, input):
        success = self.initialize_from_file(input)
        if not success:
            print("Fatal error. Giving up.")
            return None
        txt_clip = moviepy.video.VideoClip.VideoClip(make_frame=self.frame_maker, duration=self.duration())
        return txt_clip

    def write_videofile(self, input):
        txt_clip = self.make_txt_clip(input)
        if not txt_clip:
            return False

        vf = self.video_format()
        if vf in ['gif', 'mp4', 'svg']:
            video = CompositeVideoClip([txt_clip])
            if vf == 'gif':
                if not self.output_file.endswith(".gif"):
                    self.output_file += ".gif"
                video.write_gif(self.output_file, fps=c.fps())
            elif vf == 'mp4' or vf == "svg":  # if we don't write a video file/gif the system stops after a single frame
                if not self.output_file.endswith(".mp4"):
                    self.output_file += ".mp4"
                video.write_videofile(self.output_file, fps=c.fps())
        return True


if __name__ == "__main__":
    filenames = ['complex']
    for filename in filenames:
        output_file = str(Path(__file__).absolute().parent.joinpath(f"outputs/debug/{filename}"))
        c = CaptionGenerator(output_file)
        input_file = str(Path(__file__).absolute().parent.joinpath(f"examples/{filename}.toml"))
        c.write_videofile(input=input_file)
