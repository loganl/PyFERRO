# Figures are standalone documents and need the same engine as the manual
# (tikzpreamble.tex loads unicode-math, which pdflatex cannot run).  latexmk
# reads the rc file in its working directory only, not the parent's, so this
# repeats the setting from ../.latexmkrc.
$pdf_mode = 4;                       # 4 = lualatex
$lualatex = 'lualatex -interaction=nonstopmode -halt-on-error %O %S';
