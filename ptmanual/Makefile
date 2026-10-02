# ---------------------------------------------------------------------------
#  Phase Transitions lab write-up
#
#    make            build tikz figures (if needed) and then main.pdf (untagged)
#    make tagged     the same as main-tagged.pdf: tagged PDF/UA-2, screen-reader accessible
#    make figures    build every standalone TikZ figure in tikz/
#    make clean      remove LaTeX aux files (keeps the PDFs)
#    make distclean  also remove the manual PDFs and the figure PDFs
# ---------------------------------------------------------------------------

LATEX      = latexmk -lualatex -interaction=nonstopmode -halt-on-error -synctex=1
TIKZ_SRC  := $(wildcard tikz/fig-*.tex)
TIKZ_PDF  := $(TIKZ_SRC:.tex=.pdf)

.PHONY: all tagged figures clean distclean

all: main.pdf

main.pdf: main.tex $(TIKZ_PDF) tikzpreamble.tex
	$(LATEX) main.tex

# main-tagged.tex inputs main.tex with tagging switched on.
tagged: main-tagged.pdf

main-tagged.pdf: main-tagged.tex main.tex $(TIKZ_PDF) tikzpreamble.tex
	$(LATEX) main-tagged.tex

figures: $(TIKZ_PDF)

# Each standalone is compiled from inside tikz/ so that its
# \input{../tikzpreamble} resolves correctly.
tikz/%.pdf: tikz/%.tex tikzpreamble.tex
	cd tikz && $(LATEX) $*.tex

clean:
	rm -f  main.aux main.log main.out main.toc main.fls main.fdb_latexmk
	rm -f  main-tagged.aux main-tagged.log main-tagged.out main-tagged.toc main-tagged.fls main-tagged.fdb_latexmk
	rm -f  main-luamml-mathml.html main-tagged-luamml-mathml.html
	rm -f  tikz/*.aux tikz/*.log

distclean: clean
	rm -f main.pdf main-tagged.pdf $(TIKZ_PDF)
