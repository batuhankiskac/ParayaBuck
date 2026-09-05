# Screen protocol repair note

The frozen protocol remains byte unchanged at SHA-256
`39213f8aabed1a1268f36179d4cabe9cb79e678274ba6a4317b2b14911543a52`.

Attempt 01 exposed a runner adapter defect at
`portfolio_expansion_crypto_screen_2026_09.py:127`: `zip(window,
window[1:], strict=True)` compares a 23-item sequence with a 22-item sequence
and always raises. The repair is the minimal pairing correction
`zip(window[:-1], window[1:], strict=True)`. Score formula, windows, cadence,
fees, and matrix are unchanged.

The corrected runner SHA is
`8a0eb3ccb8dc9a4ebddb0b9cd2d711bb40f2b7866fab8b07fdbf757e34f53d6d`.
The corrected frozen manifest is
`data/research_manifests/portfolio_expansion_crypto_screen_2026_09_05_frozen.json`
with canonical SHA
`b26c2afae9a03bbb93fe8d09bc26ff0df087f05716659848e3ce07fd9bc56710`.
