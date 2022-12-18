from pathlib import Path
from captiongenerator import CaptionGenerator
import moviepy
from moviepy.editor import CompositeVideoClip

def main():
    c = CaptionGenerator(str(Path( __file__ ).absolute().parent.joinpath("templates")))
    current_path = Path( __file__ ).absolute().parent.joinpath("examples/complex.toml")
    c.initialize_from_file(current_path)
    c.set_enable_save_svg(True)  # debug option

    fps = 25
    txt_clip = moviepy.video.VideoClip.VideoClip(make_frame=c.frame_maker, duration=c.duration())
    video = CompositeVideoClip([txt_clip])

    # Write video file with the result
    video.write_videofile("/home/shimpe/development/python/camala/outputs/test_with_inkscape.mp4", fps=fps)

if __name__ == "__main__":
    main()