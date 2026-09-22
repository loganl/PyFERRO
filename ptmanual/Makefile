# ---------------------------------------------------------------------------
#  Phase Transitions lab write-up
#
#    make            build tikz figures (if needed) and then main.pdf
#    make figures    build every standalone TikZ figure in tikz/
#    make clean      remove LaTeX aux files (keeps the PDFs)
#    make distclean  also remove main.pdf and the figure PDFs
# ---------------------------------------------------------------------------

LATEX      = lualatex -interaction=nonstopmode -halt-on-error
TIKZ_SRC  := $(wildcard tikz/fig-*.tex)
TIKZ_PDF  := $(TIKZ_SRC:.tex=.pdf)

.PHONY: all figures clean distclean

all: main.pdf

main.pdf: main.tex $(TIKZ_PDF) tikzpreamble.tex
	$(LATEX) main.tex
	$(LATEX) main.tex
	$(LATEX) main.tex

figures: $(TIKZ_PDF)

# Each standalone is compiled from inside tikz/ so that its
# \input{../tikzpreamble} resolves correctly.
tikz/%.pdf: tikz/%.tex tikzpreamble.tex
	cd tikz && $(LATEX) $*.tex

clean:
	rm -f  main.aux main.log main.out main.toc main.fls main.fdb_latexmk
	rm -f  main-luamml-mathml.html
	rm -f  tikz/*.aux tikz/*.log

distclean: clean
	rm -f main.pdf $(TIKZ_PDF)
