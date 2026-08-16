# Vendored fonts

These two files are committed deliberately. `next/font/google` fetches from
`fonts.gstatic.com` at **build** time, which makes `next build` fail whenever Google Fonts
rate-limits the shared CI runner IPs — it did, on `main`, on a commit that had already
passed CI twice. Vendoring removes the network from the build.

| File | Family | Source | Weights |
|------|--------|--------|---------|
| `Inter-latin-variable.woff2` | Inter v20 | Google Fonts CSS2 API, `latin` subset | variable, 100–900 |
| `JetBrainsMono-latin-variable.woff2` | JetBrains Mono v24 | Google Fonts CSS2 API, `latin` subset | variable, 100–800 |

Each is the `latin` subset only, matching the `subsets: ["latin"]` the previous
`next/font/google` config requested. One variable file per family covers every weight the
site uses (Inter 400/500/600/700, JetBrains Mono 400/500).

## License

Both families are licensed under the **SIL Open Font License, Version 1.1**, which permits
redistribution and embedding, including bundled with a web application.

- Inter — © The Inter Project Authors (https://github.com/rsms/inter)
- JetBrains Mono — © The JetBrains Mono Project Authors (https://github.com/JetBrains/JetBrainsMono)

Full license text: https://openfontlicense.org/open-font-license-official-text/

Neither font is sold, and neither is distributed under a reserved font name that this
project modifies — the files are byte-identical to what Google Fonts serves.

## Updating

Re-fetch from the CSS2 API with a modern browser User-Agent (it returns woff2; an older UA
gets ttf), take the `/* latin */` block's `src` URL, and replace the file in place:

    curl -H "User-Agent: Mozilla/5.0 ... Chrome/120.0.0.0 ..." \
      "https://fonts.googleapis.com/css2?family=Inter:wght@100..900&display=swap"
