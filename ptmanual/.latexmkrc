# latexmk configuration for this manual.
#
# The document needs LuaLaTeX (unicode-math, and luamml for the MathML), so
# `latexmk` on its own is enough -- no -lualatex on the command line.  latexmk
# repeats the passes until the aux files stop changing, which also settles
# luamml's MathML cache.
# With no file name latexmk would build every .tex here, main-tagged.tex too.
@default_files = ('main.tex');
$pdf_mode = 4;                       # 4 = lualatex
$lualatex = 'lualatex -interaction=nonstopmode -halt-on-error -synctex=1 %O %S';

# A stale MathML cache stops the next pass, so let `latexmk -c` remove it.
push @generated_exts, 'synctex.gz';
$clean_ext .= ' %R-luamml-mathml.html';
