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
from dataclasses import dataclass

@dataclass
class FilterTemplate:
    svg_template: str
    defaults: dict

def to_numpy(image, width, height):
    '''  Converts an RGBA image into numpy RGB format  '''
    arr = np.array(image).reshape(height, width, 4)  # Copies the data
    return arr[:, :, :3]  # remove alpha channel


class CaptionGenerator(object):
    """
    The main class in this project. This class interprets .toml specifications and turns them into animated caption movies.
    """
    def __init__(self, output_file):
        """

        :param output_file: (full) path to where the resulting movie should be written
        """
        self.template_folder = str(Path(__file__).absolute().parent.joinpath("templates"))
        self.output_file = output_file
        self.output_folder = str(Path(output_file).parent)
        guess = defaultdict(lambda key: "")
        guess['Linux'] = '/usr/bin/inkscape'
        guess['Windows'] = r'c:\Program Files\Inkscape\Inkscape.exe'
        guess['Darwin'] = r'/Applications/Inkscape.app/Contents/MacOS/inkscape'  # ???
        self.inkscape = guess[platform.system()]
        self.frame_maker = None
        self.animations = {}
        self.filters = {}
        self.paths = {}
        self.spec = None

    def duration(self):
        """

        :return: value of the duration in seconds declared in the [Global] section of the .toml specification
        """
        return self._eval_expr(self._replace_globals('${Global.duration}'))

    def video_format(self):
        """

        :return: value of the output format declared in the [Global] section of the .toml specification (can be gif, mp4 or svg)
        """
        return self.spec['Global']['format'].lower()

    def fps(self):
        """

        :return: value of the fps (frame per second) declared in the [Global] section
        """
        return self._eval_expr(self._replace_globals('${Global.fps}'))

    def initialize_from_file(self, filename: str) -> bool:
        """

        :param filename: full path to a .toml specification
        :return: True if the initialization succeeded; False if it failed (e.g. because of syntax errors in the .toml file)
        """
        try:
            with open(filename, "r") as f:
                contents = f.read()
            return self.initialize_from_string(contents)
        except IOError as e:
            print('Error opening file ' + str(filename) + '\n' + str(e))
            return False

    def initialize_from_string(self, contents: str) -> bool:
        """

        :param contents: a string containing the .toml specification
        :return: True if the initialization succeeded; False if it failed (e.g. because of syntax errors in the .toml file)
        """
        self.spec = toml.loads(contents)
        if not self._validate_spec():
            print("Errors in specification found.")
            return False
        if not self._build_animations():
            print("Errors in animation specification found.")
            return False
        if not self._build_paths():
            print("Errors in path specification found.")
            return False
        if not self._build_filters():
            print("Errors in filter specification found.")
            return False

        self.frame_maker = self._build_make_frame(25)
        return True

    def _check_section_present(self, section: str, subspec: dict) -> bool:
        """
        helper function to see if a certain section is present in the .toml spec  (used during validation of the .toml spec)
        :param section: name of a section
        :param subspec: a dict
        :return: True if ok, False if nok
        """
        if not section in subspec:
            print(f"Error in specification: must have a [{section}] section.")
            return False
        return True

    def _check_key_present(self, key: str, sectionname: str, subspec: dict) -> bool:
        """
        helper function to see if a key is present in sectionname in subspec  (used during validation of the .toml spec)
        :param key: a string denoting the name of the key (e.g. font-size)
        :param sectionname: a string denoting the name of the section
        :param subspec: a dictionary containing part of the .toml spec
        :return: True if ok, False if nok
        """
        if not key in subspec:
            print(f"Error in specification: must have a key {key} in [{sectionname}]")
            return False
        return True

    def _check_all_keys_present(self, keys: list[str], sectionname: str, subspec: dict) -> bool:
        """
        helper function to see if all keys in a list of keys are present in sectionname in subspec  (used during validation of the .toml spec)
        :param keys: a list of string indicating which keys to check for
        :param sectionname: a string denoting the name of the section
        :param subspec: a dictionary containing part of the .toml spec
        :return:
        """
        all_ok = True
        for key in keys:
            all_ok = self._check_key_present(key, sectionname, subspec) and all_ok
        return all_ok

    def _check_all_leaves_are_strings(self, sectionname, spec) -> bool:
        """
        helper function to check syntax of the .toml spec (used during validation of the .toml spec)
        :param sectionname: a string denoting the section name
        :param spec: a dictionary containing the .toml spec
        :return: True if all values are defined as strings; False if not
        """
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
        """
        helper function to check if valid animations have been specified (used during validation of the .toml spec)
        :param spec: a dictionary containing the .toml spec
        :return: True if ok, False if nok
        """
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
        """
        helper function to check if StyleProperties are associated to a Style definition (used during validation of the .toml spec)
        :param stylesspec: part of the dictionary with the the .toml spec that contains a style definition
        :return: True if ok; False if nok
        """
        for key in stylesspec:
            if "StyleProperties" not in stylesspec[key]:
                print(f"Error! In section Styles.{key} no StyleProperties section is found.")
                return False
        return True

    def _supported_tween_methods(self):
        """
        helper function that returns a list of supported tweening options (a large subset of what vectortween supports)
        :return: a list of strings containning valid tweening options
        """
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
        """
        helper function to check if a valid tweening option is selected (for now only a subset of what vectortween supports is used)
        :param tween: string with the name of a tweening option
        :return: True of ok; False if nok

        """
        supported_tweens = self._supported_tween_methods()
        if tween not in supported_tweens:
            return False
        return True

    def _check_styles(self, spec):
        """
        helper function for validation of the .toml spec (note that many validations happen during generation as well)
        :param spec: dictionary containing the .toml specification
        :return: True if ok; False if nok
        """
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

    def _validate_spec(self) -> bool:
        """
        helper function to do a initial validation of the .toml spec (but most validations happen during generation)
        :return: True if ok; False if nok
        """
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
        """
        helper function to replace things named ${something.else.goes} with values defined in a dictionary
        this helper function is introduced because python string Templates do not support using points

        patterns that are in the_string, but not in the placeholders dict are preserved (similar to the
        safe_substitute function of python's string Template)
        :param the_string: (svg) string with patterns
        :param placeholders: dictionary containing the values for these patters
        :return: a new (svg) string in which the patterns have been replaced with the placeholders
        """
        for p in placeholders:
            the_string = the_string.replace(str(p), str(placeholders[p]))
        return the_string

    def _replace_globals(self, the_string):
        """
        takes a string and replaces occurrences of the pattern ${Global.xxxx} with the value of xxxx in the [Global] section
        in the .toml specification (this enables referencing entries in the .toml spec [Global] section while specifying animation parameters e.g.)
        :param the_string:
        :return:
        """
        placeholders = {}
        for key in self.spec['Global']:
            placeholders[f"${{Global.{key}}}"] = self.spec['Global'][key]
        return self._replace_placeholders(the_string, placeholders)

    def _eval_expr(self, expr):
        """
        takes a string and evaluates it to something numerical (i.e. number or list of numbers or a simple formula involving numbers)
        written to be safer than using bare "eval"
        :param expr: a string containing something numerical
        :return: the numerical result
        """
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
        """
        helper function to extract from a list like [${ablah.blahbal}, ${otherblah.moreblah] a list of constituents
        ${ablah.blahbal} and ${otherblah.moreblah}.
        :param string: a string containing a list
        :return: the elements in that list (as a list of strings)
        """
        return string.replace(" ", "").replace("\t", "").strip()[1:-1].split(",")

    def _collect_animations(self, kind, basic_type_name, basic_type_class):
        """
        helper function to build a lookup table self.animations of animation objects from the .toml spec
        in addition to NumberAnimations and PointAnimations, this function also collects SumAnimations and SequentialAnimations
        this function may print some warnings if specifications are incomplete and values need to be guessed
        :param kind: the type of animation (things like 'Position', 'TextProvider', 'Style', 'CaptionSvgAttribute', 'SegmentSvgAttribute'
        :param basic_type_name: do we expect a 1-D (NumberAnimation) or a 2-D (PointAnimation, e.g. for positions) animation
        :param basic_type_class: which vectortween animation class corresponds to this type of 1-D/2-D animation
        :return: True if ok; False if nok
        """
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
        """
        helper function to collect animations in the Animations.Position section
        :return: True if ok; False if nok
        """
        return self._collect_animations('Position', 'PointAnimation', PointAnimation)

    def _collect_captionsvgattribute_animations(self):
        """
        helper function to collect animations in the Animations.CaptionSvgAttribute section
        :return: True if ok; False if nok
        """
        return self._collect_animations('CaptionSvgAttribute', 'NumberAnimation', NumberAnimation)

    def _collect_segmentsvgattribute_animations(self):
        """
        helper function to collect animations in the Animations.SegmentSvgAttribute section
        :return:  True if ok; False if nok
        """
        return self._collect_animations('SegmentSvgAttribute', 'NumberAnimation', NumberAnimation)

    def _collect_style_animations(self):
        """
        helper function to collect animations in the Animations.Style section
        :return:  True if ok; False if nok
        """
        return self._collect_animations('Style', 'NumberAnimation', NumberAnimation)

    def _collect_textprovider_animations(self):
        """
        helper function to collect animations in the Animations.TextProvider section
        :return: True if ok; False if nok
        """
        return self._collect_animations('TextProvider', 'NumberAnimation', NumberAnimation)

    def _collect_filter_animations(self):
        """
        helper function to collect animations in the Animations.Filter section
        :return: True if ok; False if nok
        """
        return self._collect_animations('Filter', 'NumberAnimation', NumberAnimation)

    def _collect_path_animations(self):
        """
        helper function to collect animations in the Animations.Path section
        :return: True if ok; False if nok
        """
        return self._collect_animations('Path', 'NumberAnimation', NumberAnimation)

    def _build_animations(self):
        """
        function to search the .toml spec for animation specifications and build up an internal lookup table of animation objects
        :return:
        """
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

        self.animations['Filter'] = {}
        if not self._collect_filter_animations():
            return False

        self.animations['Path'] = {}
        if not self._collect_path_animations():
            return False

        return True

    def _build_filters(self):
        """
        function to load filter svg templates
        :return: True if ok; False if nok
        """
        self.filters = {}
        for line in self.spec['Caption']:
            if 'Filter' in self.spec['Caption'][line]:
                if not 'filter' in self.spec['Caption'][line]['Filter']:
                    print(f"Error! Caption.{line}.Filter does not contain a filter field.")
                    return False
                filter_name = self.spec['Caption'][line]['Filter']['filter'][len("${Filters."):-1]
                if filter_name not in self.filters:
                    filter_template_file = Path(self.template_folder).joinpath('filters', filter_name+".svgtemplate")
                    if not filter_template_file.is_file():
                        print(f"Error! Caption.{line}.Filter.filter does not point to an existing file {filter_template_file}.")
                        return False
                    filter_template = filter_template_file.read_text("utf-8")
                    filter_defaults_file = Path(self.template_folder).joinpath('filters', filter_name+".toml")
                    if not filter_defaults_file.is_file():
                        print(f"Error! Caption.{line}.Filter.filter does not point to an existing file {filter_defaults_file}.")
                        return False
                    try:
                        filter_defaults = toml.load(filter_defaults_file)
                    except Exception:
                        print(f"Error! Couldn't parse {filter_defaults_file}. Check for syntax errors.")
                        return False

                    self.filters[filter_name] = FilterTemplate(svg_template=filter_template, defaults=filter_defaults)
        return True

    def _build_paths(self):
        self.paths = {}
        if 'Paths' in self.spec:
            for path in self.spec['Paths']:
                self.paths[path] = True # just remember which paths exist for validation purposes later on
        return True

    def _get_text_per_segment_for_line(self, text_per_line_per_segment, line, animated_value):
        """
        helper function to return part of a larger text based on the value of animated_value
        for value 0 only 1 character is returned, for value 100 (think percentage) all characters are returned
        (note: negative animated_value returns characters from the back to the front instead of from front to back)
        extra difficulty is that the complete text may be spread over different segments, so this function needs to
        preserve these boundaries
        :param text_per_line_per_segment: datastructure containing text split in different segments per line
        :param line: current line being examined
        :param animated_value: value between -100-100 to indicate how much of the text is to be returned
        :return:
        """
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
        """
        renders the mako svg template to get a string that still contains placeholders for animated values
        :return: a tuple of status, and svg string with placeholders for animations. Status is True if ok; False if nok.
        """
        try:
            svg_text_template = Template(filename=os.path.join(self.template_folder, "doc.svgtemplate"),
                                         module_directory=os.path.join(self.template_folder, "modules"))
            return True, svg_text_template.render(spec=self.spec, thefilters=self.filters, thepaths=self.paths)
        except:
            print(exceptions.text_error_template().render())
        return False, ""

    def _parse_animation_times(self, fps, line, kind):
        """
        helper function to parse birth_time, begin_time, end_time and death_time from the .toml spec
        this variant is used in PositionAnimation and TextProviderAnimation in the Caption section of the .toml spec
        :param fps: frames per second (to convert between seconds and frames)
        :param line: which Caption.Line is being processed
        :param kind: one of 'PositionAnimation' or 'TextProviderAnimation'
        :return: tuple birth_time, begin_time, end_time and death_time
        """
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
        """
        helper function to parse birth_time, begin_time, end_time and death_time from the .toml spec
        this variant is used in StyleAnimation in the Styles section of the .toml spec
        :param fps: frames per second (to convert between seconds and frames)
        :param line: which Style.name is being processed
        :param kind: animation name being processed
        :return: tuple birth_time, begin_time, end_time and death_time
        """
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
        else:
            print(f"Warning: no Caption.{style_name}.StyleAnimation.{kind} section present. Using defaults.")

        return birth_frame, start_frame, stop_frame, death_frame

    def _parse_captionsvgattribute_animation_times(self, fps, attrib_anim_name):
        """
        helper function to parse birth_time, begin_time, end_time and death_time from the .toml spec
        this variant is used in Animations.CaptionSvgAttribute.attrib_anim_name.CaptionSvgAttributeAnimation of the .toml spec
        :param fps: frames per second (to convert between seconds and frames)
        :param attrib_anim_name: which attribute_anim_name is being processed
        :return: tuple birth_time, begin_time, end_time and death_time
        """
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

    def _parse_filter_animation_times(self, fps, animation_name, parameter_name):
        """
        helper function to parse birth_time, begin_time, end_time and death_time from the .toml spec
        this variant is used in Animations.Filter.animation_name.FilterAnimation of the .toml spec
        :param fps: frames per second (to convert between seconds and frames)
        :param animation_name: which animation_name is being processed
        :return: tuple birth_time, begin_time, end_time and death_time
        """
        birth_frame = 0
        start_frame = 0
        stop_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        death_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        if not animation_name in self.spec['Animations']['Filter']:
            print(
                f"Error! didn't find a section Animations.Filter.{animation_name}. Using birth_frame = {birth_frame}, start_frame = {start_frame}, stop_frame = {stop_frame}, death_frame = {death_frame}.")
        else:
            if 'FilterAnimation' not in self.spec['Animations']['Filter'][animation_name]:
                print(
                    f"Warning: no Animations.Filter.{animation_name}.FilterAnimation section found. Using birth_frame = {birth_frame}, start_frame = {start_frame}, stop_frame = {stop_frame}, death_frame = {death_frame}.")
            else:
                if parameter_name not in self.spec['Animations']['Filter'][animation_name]['FilterAnimation']:
                    print(f"Warning: no Animations.Filter.{animation_name}.FilterAnimation.{parameter_name} section present. Using birth_frame = {birth_frame}, start_frame = {start_frame}, stop_frame = {stop_frame}, death_frame = {death_frame}.")
                else:
                    time_section = self.spec['Animations']['Filter'][animation_name]['FilterAnimation'][parameter_name]
                    if 'birth_time' in time_section:
                        birth_frame = self._eval_expr(self._replace_globals(time_section['birth_time'])) * fps
                    else:
                        print(
                            f"Warning: no birth_time specified in Animations.Filter.{animation_name}. Using {birth_frame}.")
                    if 'begin_time' in time_section:
                        start_frame = self._eval_expr(self._replace_globals(time_section['begin_time'])) * fps
                    else:
                        print(
                            f"Warning: no start_time specified in Animations.Filter.{animation_name}. Using {start_frame}.")
                    if 'end_time' in time_section:
                        stop_frame = self._eval_expr(self._replace_globals(time_section['end_time'])) * fps
                    else:
                        print(
                            f"Warning: no stop_time specified in Animations.Filter.{animation_name}. Using {stop_frame}.")
                    if 'death_time' in time_section:
                        death_frame = self._eval_expr(self._replace_globals(time_section['death_time'])) * fps
                    else:
                        print(
                            f"Warning: no death_time specified in Animations.Filter.{animation_name}. Using {death_frame}.")
        return birth_frame, start_frame, stop_frame, death_frame

    def _parse_segmentsvgattribute_animation_times(self, fps, attrib_anim_name):
        """
        helper function to parse birth_time, begin_time, end_time and death_time from the .toml spec
        this variant is used in Animations.SegmentSvgAttribute.attrib_anim_name.SegmentSvgAttributeAnimation of the .toml spec
        :param fps: frames per second (to convert between seconds and frames)
        :param attrib_anim_name: which attribute_anim_name is being processed
        :return: tuple birth_time, begin_time, end_time and death_time
        """
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

    def _parse_path_animation_times(self, fps, line, short_name):
        """
        helper function to parse birth_time, begin_time, end_time and death_time from the .toml spec
        this variant is used in Captions.line.PathAnimation.shortname of the .toml spec
        :param fps: frames per second (to convert between seconds and frames)
        :param short_name: which animation is being processed
        :return: tuple birth_time, begin_time, end_time and death_time
        """
        birth_frame = 0
        start_frame = 0
        stop_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        death_frame = self._eval_expr(self._replace_globals('${Global.duration}')) * fps
        if not 'PathAnimation' in self.spec['Caption'][line]:
            print(f"Warning. No PathAnimation section in Caption.{line}. Using birth_frame = {birth_frame}, start_frame = {start_frame}, stop_frame = {stop_frame}, death_frame = {death_frame}.")
        elif not short_name in self.spec['Caption'][line]['PathAnimation']:
            print(
                f"Warning! didn't find a section Caption.{line}.PathAnimation.{short_name}. Using birth_frame = {birth_frame}, start_frame = {start_frame}, stop_frame = {stop_frame}, death_frame = {death_frame}.")
        else:
            time_section = self.spec['Caption'][line]['PathAnimation'][short_name]
            if 'birth_time' in time_section:
                birth_frame = self._eval_expr(self._replace_globals(time_section['birth_time'])) * fps
            else:
                print(
                    f"Warning: no birth_time specified in Caption.{line}.PathAnimation.{short_name}. Using {birth_frame}.")
            if 'begin_time' in time_section:
                start_frame = self._eval_expr(self._replace_globals(time_section['begin_time'])) * fps
            else:
                print(
                    f"Warning: no start_time specified in Caption.{line}.PathAnimation.{short_name}. Using {start_frame}.")
            if 'end_time' in time_section:
                stop_frame = self._eval_expr(self._replace_globals(time_section['end_time'])) * fps
            else:
                print(
                    f"Warning: no stop_time specified in Caption.{line}.PathAnimation.{short_name}. Using {stop_frame}.")
            if 'death_time' in time_section:
                death_frame = self._eval_expr(self._replace_globals(time_section['death_time'])) * fps
            else:
                print(
                    f"Warning: no death_time specified in Caption.{line}.PathAnimation.{short_name}. Using {death_frame}.")
        return birth_frame, start_frame, stop_frame, death_frame

    def _resolve_textprovider_animations(self, fps, current_frame, line, svg):
        """
        helper function to iterate over all Caption.Line.Segments and fill in the value of the animated properties for the value of current_frame
        :param fps: frames per second
        :param current_frame: current frame in the animation
        :param line: which caption.Line we are processing
        :param svg: svg string with placeholders
        :return: new svg string with (potentially) some placeholders replaced by values
        """
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

    def _resolve_pathproperty_animations(self, fps, current_frame, line, svg):
        if 'PathProperties' not in self.spec['Caption'][line]:
            # use defaults
            return svg
        else:
            resolved_pathanimation_values = {}
            for prop in self.spec['Caption'][line]['PathProperties']:
                if "${" in self.spec['Caption'][line]['PathProperties'][prop]:
                    short_name = self.spec['Caption'][line]['PathProperties'][prop][len("${Animations.Path."):-1]
                    if short_name not in self.animations['Path']:
                        print(
                            f"Error! Section Caption.{line}.PathProperties uses an animation {short_name} which is not defined in Animations.Path section")
                        return False
                    birth_frame, start_frame, end_frame, death_frame = self._parse_path_animation_times(fps, line, short_name)
                    current_value = self.animations['Path'][short_name].make_frame(current_frame,
                                                                                   birth_frame,
                                                                                   start_frame,
                                                                                   end_frame,
                                                                                   death_frame)
                    key = "${Animations.Path." + f"{short_name}" + "_for_line_" + f"{line}" + "}"
                    resolved_pathanimation_values[key] = current_value
            svg = self._replace_placeholders(svg, resolved_pathanimation_values)
        return svg


    def _resolve_captionsvgattribute_animations(self, fps, current_frame, line, svg):
        """
        helper function to replace animation placeholders in Caption.line.CaptionSvgAttribute with animated values for current_frame
        :param fps: frames per second
        :param current_frame: current frame in the animation
        :param line: which caption.Line we are processing
        :param svg: svg string with placeholders
        :return: new svg string with (potentially) some placeholders replaced by values
        """
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
        """
        helper function to replace animation placeholders in Caption.line.SegmentSvgAttribute with animated values for current_frame
        :param fps: frames per second
        :param current_frame: current frame in the animation
        :param line: which caption.Line we are processing
        :param segment: which segment we are processing within the line
        :param svg: svg string with placeholders
        :return: new svg string with (potentially) some placeholders replaced by values
        """
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
        """
        helper function to replace animation placeholders in Caption.line.PositionAnimation with animated values for current_frame
        :param fps: frames per second
        :param current_frame: current frame in the animation
        :param line: which caption.Line we are processing
        :param svg: svg string with placeholders
        :return: new svg string with (potentially) some placeholders replaced by values
        """
        current_pos = [0, 0]
        if 'pos' not in self.spec['Caption'][line] and 'path' not in self.spec['Caption'][line]:  # no position or path
            print(f"Warning: no position/path specified in caption Caption.{line}. Using {current_pos} instead.")
            resolved_values = {line + '_x': current_pos[0],
                               line + "_y": current_pos[1]}
            svg = string.Template(svg).safe_substitute(resolved_values)
        elif 'path' in self.spec['Caption'][line] and not 'pos' in self.spec['Caption'][line]:
            # with path specified, but no position
            resolved_values = {line + '_x': current_pos[0],
                               line + "_y": current_pos[1]}
            svg = string.Template(svg).safe_substitute(resolved_values)
        elif 'pos' in self.spec['Caption'][line]:
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
                resolved_values = {line + '_x': current_pos[0],
                                   line + "_y": current_pos[1]}
                svg = string.Template(svg).safe_substitute(resolved_values)
            else:  # fixed position
                current_pos = self._eval_expr(self._replace_globals(self.spec['Caption'][line]['pos']))
                resolved_values = {line + '_x': current_pos[0],
                                   line + "_y": current_pos[1]}
                svg = string.Template(svg).safe_substitute(resolved_values)
        return svg

    def _resolve_style_animations(self, fps, current_frame, line, svg):
        """
        helper function to replace animation placeholders in Caption.line.Segments.segment.style with animated values for current_frame
        :param fps: frames per second
        :param current_frame: current frame in the animation
        :param line: which caption.Line we are processing
        :param svg: svg string with placeholders
        :return: new svg string with (potentially) some placeholders replaced by values
        """
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

    def _resolve_filter_animations(self, fps, current_frame, line, svg):
        resolved_filter_values = {}
        if 'Filter' in self.spec['Caption'][line]:
            filter_name = self.spec['Caption'][line]['Filter']['filter'][len("${Filters."):-1]
            if 'Overrides' in self.spec['Caption'][line]['Filter']:
                for override in self.spec['Caption'][line]['Filter']['Overrides']:
                    override_value = self.spec['Caption'][line]['Filter']['Overrides'][override]
                    if "${" in override_value: # animated filter value
                        animation_name = override_value[len("${Animations.Filter."):-1]
                        if animation_name not in self.animations['Filter']:
                            print(f"Error! Caption.{line}.Filter.Overrides.{override} specifies an animation {animation_name} which was not defined in the Animations.Filter section.")
                            return False
                        animation = self.animations['Filter'][animation_name]
                        birth_frame, begin_frame, end_frame, death_frame = self._parse_filter_animation_times(fps,
                                                                                                              animation_name,
                                                                                                              override)
                        current_value = animation.make_frame(current_frame, birth_frame, begin_frame,
                                                             end_frame, death_frame)
                        resolved_filter_values["${Animations.Filter." + f"{override}_{line}" + "}"] = current_value
                    else: # fixed filter value
                        resolved_filter_values["${Animations.Filter." + f"{override}_{line}" + "}"] = override_value
            # now replace everything that was not overridden with default values
            for parameter in self.filters[filter_name].defaults['defaults']:
                key = "${Animations.Filter." + f"{parameter}_{line}" + "}"
                if key not in resolved_filter_values:
                    resolved_filter_values[key] = self.filters[filter_name].defaults['defaults'][parameter]
            svg = self._replace_placeholders(svg, resolved_filter_values)
        return svg

    def _resolve_uninstantiated_filter_animations(self, svg):
        resolved_filter_values = {}
        for line in self.spec['Caption']:
            for filter_name in self.filters:
                for parameter in self.filters[filter_name].defaults['defaults']:
                    key = "${Animations.Filter." + f"{parameter}_{line}" + "}"
                    if key not in resolved_filter_values:
                        resolved_filter_values[key] = self.filters[filter_name].defaults['defaults'][parameter]
        svg = self._replace_placeholders(svg, resolved_filter_values)
        return svg

    def _build_make_frame(self, fps):
        """
        helper function to generate a make_frame function that can be used by moviepy
        :param fps: frames per second
        :return: a function that is suitable as make_frame function in moviepy
        """
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

                svg = self._resolve_filter_animations(fps, current_frame, line, svg)
                if not svg:
                    return False

                svg = self._resolve_pathproperty_animations(fps, current_frame, line, svg)
                if not svg:
                    return False

            svg = self._resolve_uninstantiated_filter_animations(svg)
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

    def make_txt_clip(self, path_to_input_file):
        """
        function to generate a moviepy VideoClip with animated text from a .toml spec
        :param path_to_input_file: full path to .toml file
        :return: a moviepy.video.VideoClip.VideoClip
        """
        success = self.initialize_from_file(path_to_input_file)
        if not success:
            print("Fatal error. Giving up.")
            return None
        txt_clip = moviepy.video.VideoClip.VideoClip(make_frame=self.frame_maker, duration=self.duration())
        return txt_clip

    def make_txt_clip_from_string(self, input):
        """
        function to generate a moviepy VideoClip with animated text from a .toml spec
        :param input: string with contents of .toml file
        :return: a moviepy.video.VideoClip.VideoClip
        """
        success = self.initialize_from_string(input)
        if not success:
            print("Fatal error. Giving up.")
            return None
        txt_clip = moviepy.video.VideoClip.VideoClip(make_frame=self.frame_maker, duration=self.duration())
        return txt_clip

    def write_videofile(self, input):
        """
        generates video file containing the animated text (output file was specified in CaptionGenerator constructor already)
        :param input: full path to .toml spec
        :return: True if ok; False if nok
        """
        txt_clip = self.make_txt_clip(input)
        if not txt_clip:
            return False

        vf = self.video_format()
        if vf in ['gif', 'mp4', 'svg']:
            video = CompositeVideoClip([txt_clip])
            if vf == 'gif':
                if not self.output_file.endswith(".gif"):
                    self.output_file += ".gif"
                video.write_gif(self.output_file, fps=self.fps())
            elif vf == 'mp4' or vf == "svg":  # if we don't write a video file/gif the system stops after a single frame
                if not self.output_file.endswith(".mp4"):
                    self.output_file += ".mp4"
                video.write_videofile(self.output_file, fps=self.fps())
        return True


if __name__ == "__main__":
    filenames = ['simple', 'simple-colorchange', 'simple-animatedstyle', 'simple-animatedstyle2',
                 'sequential-style-animation', 'position-animation', 'position-sumanimation',
                 'complex', 'textprovider', 'howtomakeapianosing', 'thisvideomaycontaintracesofmath', 'introducing',
                 'textfilter', 'textfilters2', 'textpath', 'gradient']
    for index, filename in enumerate(filenames):
        output_file = str(Path(__file__).absolute().parent.joinpath(f"../examples/gettingstarted/outputs/{filename}"))
        print(f"[{index+1}/{len(filenames)}] Processing {output_file}.")
        c = CaptionGenerator(output_file)
        input_file = str(Path(__file__).absolute().parent.joinpath(f"../examples/gettingstarted/{filename}.toml"))
        c.write_videofile(input=input_file)
