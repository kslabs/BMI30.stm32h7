# Portal and oscilloscope localization

The shared `ui_language.js` is embedded in both Python entry points by
`patch_language.py`. Runtime localization is offline; no translation service,
model, Python package or new service is installed on the Raspberry.

Supported locale codes are `en`, `uk`, `es`, `de`, `cs`, `ja`, `fr`, `pt`,
`zh-Hans`, `it`, `ru`. English is the default when no explicit preference exists.
The language menu uses native language names. It opens after 2000 ms of pointer
hover or holding, on click, and with the keyboard. The host cookie
`bmi30_language` shares the choice with the direct oscilloscope port;
`bmi30.ui.language` in localStorage synchronizes open portal tabs.

The initial JSON catalogs were generated locally with official Argos model data.
`model_provenance.json` records the source URLs and model SHA-256 hashes.
`ru.tsv`, `reviewed.tsv`, and `noise_help.json` override the generated translations.
The complete p/n/t/SN help and the principal detector terms were reviewed for all
ten translated languages. Other long explanations are initial machine translations
and have not been checked by native speakers.

Translation preserves canonical source strings in weak maps, allowing reversal
to English and subsequent dynamic updates. Attributes and changed DOM nodes are
observed separately; the whole page is not rescanned on every acquisition frame.
Numeric input/option values, user names, SSIDs, raw file names, and identifiers
are excluded. The scope passes canonical status/tooltip strings to the shared
writer; canvas labels are translated before measuring/drawing.

`test_language.mjs` tests the actual implementation in happy-dom, including the
1999/2000 ms boundary, cancellation, click/hold/Escape, all locales, persistence,
dynamic text and restoring English, and unmodified control values in real rendered
portal/user/engineer/scope HTML. Test dependencies and language models reside only
under the temporary workspace directory.

The final complete M02 bundle is `2026-10-16-1055`. Its engine is byte-identical
to `0946`; the core backend AST is identical except for its paired engine path
and embedded HTML. Deployment uses the existing M02 identity, settings, checksum,
startup-health and rollback guards. See the Russian installation report.
