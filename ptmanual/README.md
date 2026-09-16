# Phase Transitions — Advanced Laboratory write-up (LaTeX)

An accessible LaTeX edition of the Stony Brook Advanced Laboratory write-up
*Phase Transitions: Metal–Insulator transition in VO₂ and Ferroelectric
transition in BaTiO₃* (2012 version), converted from `phasetransition_2025.pdf`.

## Layout

```
main.tex             the document
tikzpreamble.tex     TikZ/CircuiTikZ setup shared by main.tex and every figure
tikz/fig-*.tex       27 standalone TikZ figures (all line art is redrawn)
tikz/fig-*.pdf       the compiled figures, included by main.tex
figures/*.png        photographs, screenshots, portraits and the two raster plots
Makefile             build rules
```

## Building locally

```bash
make
```

`make` compiles every `tikz/fig-*.tex` to a PDF (each is a `standalone`
document and can also be compiled on its own), then runs `pdflatex` three times
on `main.tex` to settle the table of contents and cross-references.

To rebuild just the figures:

```bash
make figures
```

Requires a TeX Live 2024 or newer distribution (for the tagged-PDF engine) with
`circuitikz`, `siunitx` and `standalone`.

## Using it on Overleaf

Upload the whole directory (or push this folder as a Git repository and import
it). Then:

1. Set **`main.tex`** as the main document (Menu → Main document).
2. Set the compiler to **pdfLaTeX** (Menu → Compiler) and the TeX Live version
   to **2024 or later** (Menu → TeX Live version) — the accessibility features
   need it.
3. Compile.

`main.tex` includes the figures as the pre-built `tikz/fig-*.pdf` files, so
Overleaf does not need to compile them and no `--shell-escape` is required. If
you edit a figure's `.tex` on Overleaf, either compile that file on its own
there (open it and press Recompile with it set as main document) or rebuild
locally with `make figures` and re-upload the PDF.

## Accessibility

The document is built as a **tagged PDF 2.0 conforming to PDF/UA-2**:

- `\DocumentMetadata{...}` at the top of `main.tex` turns on the LaTeX tagging
  engine (`testphase = {phase-III, math, graphic, table, firstaid}`), declares
  the document language as `en-US`, and requests the PDF/UA-2 standard.
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
- The band-structure figure is the only one that uses colour; the bands are
  also distinguished by position and by text labels, so no information is
  carried by colour alone.

Adding a new figure: use `\tikzfig{<file base>}{<alt text>}` for a TikZ figure
or `\photo{<file>}{<alt text>}` for a raster image. Both take the alt text as a
required argument, so a figure cannot be added without one.

## Source

Sections on ferroelectricity in BaTiO₃ by Laszlo Mihaly and in part Michael
Gurvitch; sections on phase-transition theory and the metal–insulator
transition in VO₂ by Michael Gurvitch, both 2012.
