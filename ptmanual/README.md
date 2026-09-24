# Phase Transitions — Advanced Laboratory write-up (LaTeX)

An accessible LaTeX edition of the Stony Brook Advanced Laboratory write-up
*Phase Transitions: Metal–Insulator transition in VO₂ and Ferroelectric
transition in BaTiO₃* (2012 write-up, 2026 edition), converted from
`phasetransition_2025.pdf`.

## Layout

```
main.tex             the document; builds without tags (faster, smaller PDF)
main-tagged.tex      builds the same document as a tagged PDF/UA-2 file
tikzpreamble.tex     TikZ/CircuiTikZ setup shared by main.tex and every figure
tikz/fig-*.tex       28 standalone TikZ figures (all line art is redrawn)
tikz/fig-*.pdf       the compiled figures, included by main.tex
figures/*.png        photographs, screenshots, portraits and the two raster plots
Makefile             build rules
.latexmkrc           engine settings for latexmk (tikz/ has its own copy)
```

## Building locally

```bash
make            # main.pdf, without tags
make tagged     # main-tagged.pdf, the tagged, screen-reader-accessible edition
```

Both are the same 59 pages. The untagged one is the default because it compiles
about twice as fast (roughly 4 s a pass against 7-8 s) and the file is about 14%
smaller (4.4 MB against 5.1 MB); the tagged one
is what to hand to a reader using a screen reader (see [Accessibility](#accessibility)).

`make` compiles every `tikz/fig-*.tex` to a PDF (each is a `standalone`
document and can also be compiled on its own), then runs `lualatex` three times
on `main.tex` to settle the table of contents, the cross-references and the
MathML cache.

One command, without make (it repeats the passes itself, but does not rebuild
the figures -- they are committed, so that rarely matters):

```bash
latexmk                    # main.pdf
latexmk main-tagged.tex    # main-tagged.pdf
```

`.latexmkrc` selects LuaLaTeX with `$pdf_mode = 4`, so no `-lualatex` is needed,
and because latexmk reads a personal `~/.latexmkrc` first and the working
directory's second, the setting here wins on any machine. `tikz/.latexmkrc`
repeats it for the figures: latexmk reads the rc file in its working directory
only, never the parent's. `latexmk -c` cleans up afterwards, the MathML cache
included.

Every `.tex` file here also opens with

```
% !TEX program = lualatex
```

so TeXShop, TeXstudio and TeXworks pick the engine from the file itself.

### In VS Code

LaTeX Workshop needs one thing chosen by hand. Its default recipe passes
`-pdf` to latexmk, and a command-line engine flag overrides the rc file, so the
build would run pdflatex and die on `unicode-math`. Pick the built-in recipe
**latexmk (latexmkrc)** instead -- it passes nothing but the file name and
leaves the choice to `.latexmkrc` -- either from Build LaTeX project → Recipe
or by setting `latex-workshop.latex.recipe.default`. **latexmk (lualatex)**
works too. The `% !TEX program` line is ignored unless you also set
`latex-workshop.latex.build.forceRecipeUsage` to `false`, and even then it gets
a single pass rather than as many as the document needs, so the recipe is the
better route here. None of this rebuilds the figures; `make` does that.

On Windows: `make` exists inside WSL but not in a native Windows TeX Live, so
latexmk is the way there. If TeX Live lives in WSL, VS Code has to be inside
WSL too — install the WSL extension and reopen the folder with **Connect to
WSL** — otherwise LaTeX Workshop looks for `lualatex` on the Windows side and
finds nothing. MiKTeX users also need Strawberry Perl, which latexmk is
written in; TeX Live's own Windows installer bundles it.

To rebuild just the figures:

```bash
make figures
```

### What you need

**LuaLaTeX from TeX Live 2024 or newer.** The tagging engine and `luamml` (which
writes the MathML) are too new for the TeX Live in Debian's and Ubuntu's
archives, so install TeX Live upstream rather than through `apt`:

```bash
sudo apt install perl wget fontconfig
wget https://mirror.ctan.org/systems/texlive/tlnet/install-tl-unx.tar.gz
tar xzf install-tl-unx.tar.gz && cd install-tl-*
sudo perl ./install-tl --scheme=full --no-interaction
```

Then put its binaries on the path (adjust the year and architecture):

```bash
echo 'export PATH=/usr/local/texlive/2025/bin/x86_64-linux:$PATH' >> ~/.bashrc
```

`--scheme=full` is about 8 GB. `--scheme=small` plus

```bash
sudo tlmgr install circuitikz siunitx standalone unicode-math microtype booktabs latexmk
```

is enough for this document and much smaller.

If you would rather use the distribution's packages, the ones this document
draws on are `texlive-luatex`, `texlive-latex-recommended`,
`texlive-latex-extra` (`standalone`, `unicode-math`), `texlive-pictures`
(`circuitikz`) and `texlive-science` (`siunitx`) — but expect the
`\DocumentMetadata` line to fail on anything older than TeX Live 2024.

## Using it on Overleaf

Upload the whole directory (or push this folder as a Git repository and import
it). Then:

1. Set **`main.tex`** as the main document (Menu → Main document).
2. Set the compiler to **LuaLaTeX** (Menu → Compiler) and the TeX Live version
   to **2024 or later** (Menu → TeX Live version) — the accessibility features
   need it.
3. Compile.

For the tagged PDF set **`main-tagged.tex`** as the main document in step 1.

`main.tex` includes the figures as the pre-built `tikz/fig-*.pdf` files, so
Overleaf does not need to compile them and no `--shell-escape` is required. If
you edit a figure's `.tex` on Overleaf, either compile that file on its own
there (open it and press Recompile with it set as main document) or rebuild
locally with `make figures` and re-upload the PDF.

## Accessibility

`main-tagged.pdf` is a **tagged PDF 2.0 conforming to PDF/UA-2**. `main.pdf` is the
same document without the tags: no structure tree, no alt text, no PDF/UA claim.
Use the tagged one for anyone who reads with a screen reader.

- `\DocumentMetadata{...}` at the top of `main.tex` declares the document language
  as `en-US` and loads the tagging engine (`testphase = {phase-III, math, graphic,
  table, firstaid}`). `main-tagged.tex` defines `\tagged` before inputting
  `main.tex`, which adds `pdfstandard = ua-2`; without it the file adds
  `tagging = off` instead. That switch must come *after* `testphase`, because
  `testphase` itself turns tagging on and would override an earlier `off`, and the
  key list is assembled in a macro because `\DocumentMetadata` does not expand macros
  inside it.
- Every one of the 45 images — both the TikZ figures and the photographs —
  carries **alternative text** describing what it shows, supplied through the
  `alt=` key of `\includegraphics` (see the `\tikzfig` and `\photo` macros in
  the preamble). The alt text describes the physics content of each diagram,
  not just its appearance.
- Headings, lists, tables, figures, captions, formulas and links are all
  emitted as real structure elements, so a screen reader can navigate the
  document and the reading order is explicit.
- A table of contents, numbered sections and `hyperref` bookmarks give
  keyboard- and screen-reader-friendly navigation.
- Maths is set with `unicode-math` under LuaLaTeX, so every formula carries
  real Unicode characters and `luamml` attaches MathML to it. A percent sign
  must not reach maths directly — `\percent` is redefined as `\text{\%}`,
  because an unescaped `%` in `luamml`'s cache file comments out the rest of
  the line and breaks the next compilation pass.
- The band-structure figure is the only one that uses colour; the bands are
  also distinguished by position and by text labels, so no information is
  carried by colour alone.

Adding a new figure: use `\tikzfig{<file base>}{<alt text>}` for a TikZ figure
or `\photo{<file>}{<alt text>}` for a raster image. Both take the alt text as a
required argument, so a figure cannot be added without one.

## Source

Sections on ferroelectricity in BaTiO₃ by Laszlo Mihaly and in part Michael
Gurvitch; sections on phase-transition theory and the metal–insulator
transition in VO₂ by Michael Gurvitch, both 2012. The 2026 edition was typeset
into LaTeX with Claude Opus 4.5 and updated by Logan Levack.
