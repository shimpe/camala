.. role:: toml(code)
   :language: toml

Getting Started
===============

Camala (CAption MArkup LAnguage) is a system to generate animated captions.
It uses svg, moviepy and inkscape to generate movies from a .toml document
with caption generation instructions.

First caption
-------------

The Camala language consists of a number of sections with caption generation instruction, some of which are optional.
The smalles possible camala specification looks as follows:

.. literalinclude:: ../examples/gettingstarted/simple.toml
  :language: toml

The result of this first toml specification is

.. image:: ../examples/gettingstarted/outputs/simple.gif

1. there's a :toml:`[Global]` section. In this section some properties of the generated movie clip are setup. These properties include:
   - W for width [pixels]
   - H for height [pixels]
   - duration [seconds]
   - fps [1/seconds] (frames per second - useful in case animations are present)
   - format [string] (allowed formats: svg, gif, mp4)
   - background [#rrggbb hex color or color name]
#. there's an :toml:`Animations` section. This section must always be present, but can be left empty
#. there's a :toml:`Styles.name.StyleProperties` section. In the Styles section, we define how captions look. Here you can specify anything you could also specify in CSS (inkscape will do the final interpretation). Typical keys you define here are `fill` for letter color, `stroke` for outline color, `stroke-width` for outline thickness, `font-size` for font size, `font-family` for font name, `font-style` for normal/oblique, and many others.
#. there's a :toml:`Caption` section which describes the text that must appear. In this case, the text consists of a single line with position [0, 0] and it uses the normal style defined in the Styles section. In general, captions can consist of multiple lines (each with their own position), and every line can consist of multiple segments, each with their own style.

Changing font color
-------------------
Suppose you want to change the color of the text mid-way the sentence. This can be accomplished by defining an extra style and dividing the line in different segments as shown below:

.. literalinclude:: ../examples/gettingstarted/simple-colorchange.toml
   :language: toml

The result of this toml specification is

.. image:: ../examples/gettingstarted/outputs/simple-colorchange.gif

Animating style parameters
--------------------------
Suppose you want the text "in the middle" to change size over time. This can be done by specifying an animation in the Animations section,
and referring to it from the Styles section.
.. literalinclude:: ../examples/gettingstarted/simple-animatedstyle.toml

The result of this toml specification is

.. image:: ../examples/gettingstarted/outputs/simple-animatedstyle.gif

Notice how the :toml:`font-size` now has a value of :toml:`"${Animations.Style.grow}"`, and the :toml:`Animations.Style.grow` section
describes a NumberAnimation from 0 to 50 with tweening.
In the Style definition where the animation is used, there's now also a StyleAnimation section added, which says when the
animation starts animating, and when it stops animating.

Animating multiple style parameters
-----------------------------------
You can animate multiple style parameters independently. E.g. here I also change the letter-spacing of the first line segment.

.. literalinclude:: ../examples/gettingstarted/simple-animatedstyle2.toml

.. image:: ../examples/gettingstarted/outputs/simple-animatedstyle2.gif

Sequencing style animations
---------------------------
You can sequence different animations together to get a new animation by using SequentialAnimation instead of NumberAnimation.
E.g. here's an example of sequencing a grow and shrink animation to get a pulsing animation.

.. literalinclude:: ../examples/gettingstarted/sequential-style-animation.toml

.. image:: ../examples/gettingstarted/outputs/sequential-style-animation.gif

Notice how the SequentialAnimation takes different parameters from a NumberAnimation. SequentialAnimation takes a list of animations and
will perform these one after the other.

 - :toml:`elements` contains the list of animations to sequence
 - :toml:`time_weights` describes how much of the total animation time should be spent in each of the child animations
 - :toml:`repeats` says how often the sequence must be repeated
 - :toml:`tween` allows to specify an extra tweening on top of the already present tweening in the child animations

Animating the position
----------------------

In addition to animating the appearance of the text it's also possible to animate the position of the text. This is accomplished by adding a
Position section in the Animations and referring to it from the Caption.line pos field.

.. literalinclude:: ../examples/gettingstarted/position-animation.toml
.. image:: ../examples/gettingstarted/outputs/position-animation.gif

Summing animations
-------------------

If you want to lower two lines from the top of the screen to the bottom of the screen, maybe you want to reuse the same top_to_bottom animation,
but specify a constant offset between the positions of the first and second line. This is possible with a SumAnimation.

.. literalinclude:: ../examples/gettingstarted/position-sumanimation.toml
.. image:: ../examples/gettingstarted/outputs/position-sumanimation.gif

Of course you can also sum more complex animations.

TextProvider and TextProviderAnimation
--------------------------------------
Sometimes you may want text to appear or disappear character by character (e.g. like in a typewriter effect).
In Camala this is possible by creating an Animations.TextProvider.name animation. Such Animation must generate
numbers between 0 and 100 where 0 means that a single character is shown, and 100 means 100% of all characers are shown.
You can also generate negative numbers up to -100. If the animation produces a negative value V than the last V pct of
characters are shown instead of the first V pct of characters. Here's an example:

.. literalinclude:: ../examples/gettingstarted/textprovider.toml
.. image:: ../examples/gettingstarted/outputs/textprovider.gif

Playing with CaptionSvgAttribute and SegmentSvgAttribute
--------------------------------------------------------

The animated captions are made by generating an svg for every frame, and rendering it to png with inkscape (and later to .gif or .mp4 with moviepy).
If desired  you can directly inject some attributes into the <text> and <tspan> elements.
A Caption is translated into <text>; a segment is translated into <tspan>.
Some attributes cannot be specified in a style (or rather: you can specify them but they don't work as expected). One such
example is the "rotate" attribute. If you want to rotate characters with the rotate attribute, you need to specify it with a
CaptionSvgAttribute or a SegmentSvgAttribute. CaptionSvgAttribute and SegmentSvgAttribte can be animated. See here a complex example
combining many of the techniques mentioned before:

.. literalinclude:: ../examples/gettingstarted/complex.toml
.. image:: ../examples/gettingstarted/outputs/complex.gif

Examples from real life videos
==============================

.. literalinclude:: ../examples/gettingstarted/howtomakeapianosing.toml
.. image:: ../examples/gettingstarted/outputs/howtomakeapianosing.gif

.. literalinclude:: ../examples/gettingstarted/thisvideomaycontaintracesofmath.toml
.. image:: ../examples/gettingstarted/outputs/thisvideomaycontaintracesofmath.gif
