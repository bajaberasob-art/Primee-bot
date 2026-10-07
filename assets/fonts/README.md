# Rank-card fonts

Bundled fonts avoid relying on host font installation:

- DejaVu Sans Regular and Bold: Latin, Greek, Cyrillic, Arabic and common symbols.
  Source: Matplotlib's vendored DejaVu distribution, `lib/matplotlib/mpl-data/fonts/ttf/`.
  License: `LICENSE_DEJAVU`.
- Noto Emoji: monochrome emoji fallback.
  Source: Google Fonts, `ofl/notoemoji/NotoEmoji[wght].ttf`.
  License: `OFL-NotoEmoji.txt` (SIL Open Font License).

Arabic is shaped with arabic-reshaper and python-bidi because the deployed Pillow
build need not include libraqm. Fonts do not guarantee every Unicode glyph;
complex joined emoji and CJK scripts are outside this font set's full coverage.