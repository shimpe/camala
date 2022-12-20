import toml
from mako.template import Template
from mako import exceptions
import os.path
from vectortween.NumberAnimation import NumberAnimation
from vectortween.PointAnimation import PointAnimation
from vectortween.SequentialAnimation import SequentialAnimation
import string
import math
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
    def __init__(self):
        self.template_folder = str(Path(__file__).absolute().parent.joinpath("templates"))
        self.frame_maker = None

    def duration(self):
        return self._eval_expr(self._replace_globals('${Global.duration}'))

    def video_format(self):
        return self.spec['Global']['format'].tolower()

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
        if not self.build_animations():
            print("Errors in animation specification found.")
            return False
        self.frame_maker = self.build_make_frame(25)
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
                if the_type not in ['PointAnimation', 'SequentialAnimation']:
                    print(
                        f"Error: Position animations must be of type 'PositionAnimation' or 'SequentialAnimation'.\nAnimations.Position.{pos_anim} specifies type '{the_type}' instead.")
                    return False
        if 'Style' in spec:
            for style_anim in spec['Style']:
                anim_spec = spec['Style'][style_anim]
                if 'type' not in anim_spec:
                    print(f"Error! Animation Animations.Style.{style_anim} does not specify a type.")
                    return False
                the_type = spec['Style'][style_anim]['type']
                if the_type not in ['NumberAnimation', 'SequentialAnimation']:
                    print(
                        f"Error: Style animations must be of type 'NumberAnimation' or 'SequentialAnimation'.\nAnimations.Style.{style_anim} specifies type '{the_type}' instead.")
                    return False
        return True

    def _check_styles_properties(self, stylesspec):
        for key in stylesspec:
            if f"{key}.StyleProperties" not in stylesspec:
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

    def build_animations(self):
        self.animations = {}
        self.animations['Position'] = {}
        self.animations['Style'] = {}
        if 'Position' in self.spec['Animations']:
            # first collect all point animations
            for pos_anim in self.spec['Animations']['Position']:
                pa = self.spec['Animations']['Position'][pos_anim]
                the_type = pa['type']
                if the_type == "PointAnimation":
                    if 'begin' not in pa:
                        print(f"Warning: no 'begin' specified in Animations.Position.{pos_anim}. Using [0, 0] instead.")
                    begin_str = self._replace_globals(pa['begin']) if 'begin' in pa else '[0, 0]'
                    begin_numeric = self._eval_expr(begin_str)
                    if not isinstance(begin_numeric, list) or len(begin_numeric) != 2:
                        print(
                            f"Invalid expression in Animations.Position.{pos_anim}.begin. Expected to find a list of 2 values. Found {begin_numeric} instead.")
                        return False
                    if 'end' not in pa:
                        print(f"Warning: no 'end' specified in Animations.Position.{pos_anim}. Using [0, 0] instead.")
                    end_str = self._replace_globals(pa['end']) if 'end' in pa else '[0, 0]'
                    end_numeric = self._eval_expr(end_str)
                    if not isinstance(end_numeric, list) or len(end_numeric) != 2:
                        print(
                            f"Invalid expression in Animations.Position.{pos_anim}.end. Expected to find a list of 2 values. Found {end_numeric} instead.")
                        return False
                    tween = pa['tween'] if 'tween' in pa else 'linear'
                    if not self._check_valid_tween(tween):
                        print(
                            f"Invalid tween method in Animations.Position.{pos_anim}.tween. Expected one of {self._supported_tween_methods()}")
                        return False
                    ytween = pa['ytween'] if 'ytween' in pa else 'linear'
                    if not self._check_valid_tween(ytween):
                        print(
                            f"Invalid tween method in Animations.Position.{pos_anim}.ytween. Expected one of {self._supported_tween_methods()}")
                        return False
                    self.animations['Position'][pos_anim] = PointAnimation(begin_numeric, end_numeric, [tween],
                                                                           [ytween])

            # then collect all sequential animations
            for pos_anim in self.spec['Animations']['Position']:
                pa = self.spec['Animations']['Position'][pos_anim]
                the_type = pa['type']
                if the_type == "SequentialAnimation":
                    elements_str = self._listel_from_str(pa['elements'])
                    elements = []
                    for el in elements_str:
                        stripped_el = el[len("${Animations.Position."):-1]
                        if stripped_el not in self.animations['Position']:
                            print(
                                f"Error: sequential animation Animations.Position.{pos_anim} uses another animation named {el[2:-1]} which is not defined yet.")
                            return False
                        elements.append(self.animations['Position'][stripped_el])
                    timeweights_str = self._replace_globals(pa['time_weights']) if 'time_weights' in pa else None
                    timeweights = self._eval_expr(timeweights_str) if timeweights_str else None
                    tween = pa['tween'] if 'tween' in pa else 'linear'
                    if not self._check_valid_tween(tween):
                        print(
                            f"Invalid tween method in Animations.Position.{pos_anim}.tween. Expected one of {self._supported_tween_methods()}")
                        return False
                    if timeweights is not None and len(timeweights) != len(elements_str):
                        print(
                            f"Timeweights must have same length as elements in Animations.Position.{pos_anim}.time_weights. Currently len(elements) = {len(elements_str)}, but len(time_weights) = {len(timeweights)}")
                        return False
                    repeats = self._replace_globals(pa['repeats']) if 'repeats' in pa else '1'
                    repeats = self._eval_expr(repeats)
                    self.animations['Position'][pos_anim] = SequentialAnimation(
                        list_of_animations=elements,
                        timeweight=timeweights,
                        repeats=repeats,
                        tween=[tween])

        if 'Style' in self.spec['Animations']:
            # first collect all point animations
            for style_anim in self.spec['Animations']['Style']:
                pa = self.spec['Animations']['Style'][style_anim]
                the_type = pa['type']
                if the_type == "NumberAnimation":
                    if 'begin' not in pa:
                        print(f"Warning: no 'begin' specified in Animations.Style.{style_anim}. Using 0 instead.")
                    begin_str = self._replace_globals(pa['begin']) if 'begin' in pa else '0'
                    begin_numeric = self._eval_expr(begin_str)
                    if not isinstance(begin_numeric, (int, float)):
                        print(
                            f"Invalid expression in Animations.Style.{style_anim}.begin. Expected to find a single number. Found {begin_numeric} instead.")
                        return False
                    if 'end' not in pa:
                        print(f"Warning: no 'end' specified in Animations.Style.{style_anim}. Using 0 instead.")
                    end_str = self._replace_globals(pa['end']) if 'end' in pa else '0'
                    end_numeric = self._eval_expr(end_str)
                    if not isinstance(end_numeric, (int, float)):
                        print(
                            f"Invalid expression in Animations.Style.{style_anim}.end. Expected to find a single number. Found {end_numeric} instead.")
                        return False
                    tween = pa['tween'] if 'tween' in pa else 'linear'
                    if not self._check_valid_tween(tween):
                        print(
                            f"Invalid tween method in Animations.Style.{style_anim}.tween. Expected one of {self._supported_tween_methods()}")
                        return False
                    self.animations['Style'][style_anim] = NumberAnimation(begin_numeric, end_numeric, [tween])

            # then collect all sequential animations
            for style_anim in self.spec['Animations']['Style']:
                pa = self.spec['Animations']['Style'][style_anim]
                the_type = pa['type']
                if the_type == "SequentialAnimation":
                    elements_str = self._listel_from_str(pa['elements'])
                    elements = []
                    for el in elements_str:
                        stripped_el = el[len("${Animations.Style."):-1]
                        if stripped_el not in self.animations['Style']:
                            print(
                                f"Error: sequential animation Animations.Style.{style_anim} uses another animation named {el[2:-1]} which is not defined yet.")
                            return False
                        elements.append(self.animations['Style'][stripped_el])

                    timeweights_str = self._replace_globals(pa['time_weights']) if 'time_weights' in pa else None
                    timeweights = self._eval_expr(timeweights_str) if timeweights_str else None
                    if timeweights is not None and len(timeweights) != len(elements_str):
                        print(
                            f"Timeweights must have same length as elements in Animations.Style.{style_anim}.time_weights. Currently len(elements) = {len(elements_str)}, but len(time_weights) = {len(timeweights)}")
                        return False
                    repeats = self._replace_globals(pa['repeats']) if 'repeats' in pa else '1'
                    repeats = self._eval_expr(repeats)
                    tween = pa['tween'] if 'tween' in pa else 'linear'
                    if not self._check_valid_tween(tween):
                        print(
                            f"Invalid tween method in Animations.Style.{style_anim}.tween. Expected one of {self._supported_tween_methods()}")
                        return False
                    self.animations['Style'][style_anim] = SequentialAnimation(
                        list_of_animations=elements,
                        timeweight=timeweights,
                        repeats=repeats,
                        tween=[tween])
        return True

    def make_svg_string(self) -> bool:
        try:
            svg_text_template = Template(filename=os.path.join(self.template_folder, "doc.svgtemplate"),
                                         module_directory=os.path.join(self.template_folder, "modules"))
            return True, svg_text_template.render(spec=self.spec)
        except:
            print(exceptions.text_error_template().render())
        return False, ""

    def build_make_frame(self, fps):
        def make_frame(t):
            current_frame = t * fps
            last_frame = math.ceil(float(self.spec['Global']['duration']) * fps)
            success, svg = self.make_svg_string()
            if not success:
                print(f"Error rendering svg template.")
                return False

            # resolve the different positions and position animations
            for caption in self.spec['Caption']:

                if 'x_offset' not in self.spec['Caption'][caption]:
                    x_offset = 0
                elif "${" in self.spec['Caption'][caption]['x_offset']:
                    x_offset = 0 #  todo animated offset
                else:
                    x_offset = self._eval_expr(self._replace_globals(self.spec['Caption'][caption]['x_offset']))

                if 'y_offset' not in self.spec['Caption'][caption]:
                    y_offset = 0
                elif "${" in self.spec['Caption'][caption]['y_offset']:
                    y_offset = 0 #  todo animated offset
                else:
                    y_offset = self._eval_expr(self._replace_globals(self.spec['Caption'][caption]['y_offset']))

                if not 'pos' in self.spec['Caption'][caption]:  # no position
                    print(f"Warning: no position specified i caption Caption.{caption}. Using [0, 0] instead.")
                    resolved_values = {caption + '_x': current_pos[0] + x_offset,
                                       caption + "_y": current_pos[1] + y_offset}
                    svg = string.Template(svg).safe_substitute(resolved_values)
                else:
                    birth_frame = None
                    start_frame = None
                    stop_frame = None
                    death_frame = None
                    cap = self.spec['Caption'][caption]
                    if '${' in cap['pos']:  # animated position
                        if not 'PositionAnimation' in cap:
                            print(
                                f"Error: animated position specified, but no PositionAnimation section present in Caption.{caption}.")
                            return False
                        pa = cap['PositionAnimation']
                        if 'birth_time' in pa:
                            birth_frame = self._eval_expr(self._replace_globals(pa['birth_time'])) * fps
                        else:
                            print(
                                f"Warning: no birth_time specified in Caption.{caption}.PositionAnimation. Using None.")
                        if 'begin_time' in pa:
                            start_frame = self._eval_expr(self._replace_globals(pa['begin_time'])) * fps
                        else:
                            print(
                                f"Warning: no start_time specified in Caption.{caption}.PositionAnimation. Using None.")
                        if 'end_time' in pa:
                            stop_frame = self._eval_expr(self._replace_globals(pa['end_time'])) * fps
                        else:
                            print(
                                f"Warning: no stop_time specified in Caption.{caption}.PositionAnimation. Using None.")
                        if 'death_time' in pa:
                            death_frame = self._eval_expr(self._replace_globals(pa['death_time'])) * fps
                        else:
                            print(
                                f"Warning: no death_time specified in Caption.{caption}.PositionAnimation. Using None.")

                        the_pos = self.spec['Caption'][caption]['pos']
                        if "[" in the_pos:
                            the_pos_el = self._listel_from_str(the_pos)
                        else:
                            the_pos_el = [the_pos]
                        current_pos = [0, 0]
                        for index, animation in enumerate(the_pos_el):
                            if '${Animations.Position' in animation:
                                if len(the_pos_el) != 1:
                                    print(
                                        f"Error! Position animation in Caption.{caption}.pos must be a single animation, or a list of 2 floats.")
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
                                        f"Error! Position animation in Caption.{caption}.pos must be a single animation, or a list of 2 floats.")
                                    return False
                                animation_value = float(animation)
                                current_pos[index] = animation_value
                        resolved_values = {caption + '_x': current_pos[0] + x_offset, caption + "_y": current_pos[1] + y_offset}
                        svg = string.Template(svg).safe_substitute(resolved_values)
                    else:  # fixed position
                        current_pos = self._eval_expr(self._replace_globals(self.spec['Caption'][caption]['pos']))
                        resolved_values = {caption + '_x': current_pos[0] + x_offset, caption + "_y": current_pos[1] + y_offset}
                        svg = string.Template(svg).safe_substitute(resolved_values)

                # resolve style animations
                for segment in self.spec['Caption'][caption]['Segments']:
                    birth_frame = 0
                    death_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
                    begin_frame = None
                    end_frame = None
                    if 'style' in self.spec['Caption'][caption]['Segments'][segment]:
                        style_name = self.spec['Caption'][caption]['Segments'][segment]['style']
                        style_name_short = style_name[len("${Styles."): -1]
                        if style_name_short not in self.spec['Styles']:
                            print(
                                f"Error! section Caption.{caption}.Segments.{segment} uses a style name {style_name} which has not been defined in the Styles section.")
                            return False
                        style_definition = self.spec['Styles'][style_name_short]
                        for property in style_definition['StyleProperties']:
                            prop_val = style_definition['StyleProperties'][property]
                            if "${" in prop_val:  # animated property

                                property_animation_short = prop_val[len("${Animations.Style."):-1]
                                if 'StyleAnimation' in style_definition and\
                                        property_animation_short in style_definition['StyleAnimation']:
                                    style_def_anim = style_definition['StyleAnimation'][property_animation_short]
                                    begin_frame = self._eval_expr(
                                        self._replace_globals(style_def_anim['begin_time'])) * fps
                                    end_frame = self._eval_expr(self._replace_globals(style_def_anim['end_time'])) * fps
                                else:
                                    begin_frame = 0
                                    end_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps

                                if property_animation_short in self.animations['Style']:
                                    # animated style
                                    animation = self.animations['Style'][property_animation_short]
                                    animated_value = animation.make_frame(current_frame,
                                                                          birth_frame,
                                                                          begin_frame,
                                                                          end_frame,
                                                                          death_frame)
                                    resolved_style_values = {
                                        "${Animations.Style." + property_animation_short + "}": animated_value}
                                    svg = self._replace_placeholders(svg, resolved_style_values)
                                else:
                                    # unknown animated property
                                    print(
                                        f"section Caption.{caption}.Segments.{segment}.StyleProperties.{property} uses an animation {style_definition['StyleProperties'][property]} that was not defined in the Animation.Styles section.")
                                    return False
                            else:
                                # fixed property -> nothing to resolve
                                pass
                    else:
                        # todo: use a default style? for now, use no style (which is also a kind of default I guess)
                        begin_frame = 0
                        end_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps

            if "${" in svg:
                print(
                    f"Error! Some animations or globals could not be resolved. Please check your specification for typos.")
                print(f"{svg}")
                return False

            W = self._eval_expr(self._replace_globals('${Global.W}'))
            H = self._eval_expr(self._replace_globals('${Global.H}'))
            inkscape = "/usr/bin/inkscape"
            result = subprocess.run([inkscape,
                                     '--export-background=black',
                                     '--export-type=png',
                                     '--export-filename=-',
                                     f'--export-width={W}',
                                     f'--export-height={H}',
                                     '--pipe'],
                                    input=svg.encode(),
                                    capture_output=True)
            pngdata = result.stdout
            img = PIL.Image.open(io.BytesIO(pngdata), formats=["PNG"])

            if self.video_format() == 'svg':
                with open(os.path.join(self.template_folder, "..", "sandbox", f"frame_{int(t*fps):05}.svg"), "w") as f:
                    f.write(svg)

            return to_numpy(img, W, H)

        return make_frame

    def write_videofile(self, input, output, debug=False):
        success = self.initialize_from_file(input)
        if not success:
            print("Fatal error. Giving up.")
            return False
        self.set_enable_save_svg(debug)
        vf = self.video_format()
        if vfin ['gif', 'mp4']:
            txt_clip = moviepy.video.VideoClip.VideoClip(make_frame=self.frame_maker, duration=self.duration())
            video = CompositeVideoClip([txt_clip])
            if vf == 'gif':
                video.write_gif(output, fps=c.fps())
            else if vf == 'mp4':
                video.write_videofile(output, fps=c.fps())

if __name__ == "__main__":
    c = CaptionGenerator()
    input_file = str(Path(__file__).absolute().parent.joinpath("examples/thisvideomaycontaintracesofmath.toml"))
    output_file = str(Path(__file__).absolute().parent.joinpath("outputs/thisvideomaycontaintracesofmath.mp4"))
    c.write_videofile(input=input_file, output=output_file)

