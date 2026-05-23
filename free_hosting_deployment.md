# Free Hosting Deployment

The live domain currently points at Google Frontend A/AAAA records. Move it only after the new service URL is healthy.

## Recommended Free Path

Use a Docker web service because this app needs Flask, NumPy, GRIB decoding, and live upstream weather fetches.

### Render

1. Create a new Render Blueprint from this repo.
2. Render will read `render.yaml` and create the `model-forecast` Docker web service on the free plan.
3. Wait for `/api/health` on the `*.onrender.com` URL to return `200`.
4. Add `modelforecastpy.app` as a custom domain.
5. In DNS, remove the Google A/AAAA records and add the records Render shows.
6. Verify:
   - `https://modelforecastpy.app/`
   - `https://modelforecastpy.app/api/health`
   - `https://modelforecastpy.app/api/models`
   - one small `/api/forecast?...` request

### Hugging Face Docker Space

Use this if Render free memory is too small. Create a public Docker Space and push this repo to it with this README front matter at the top of the Space README:

```yaml
---
title: Model Forecast
colorFrom: blue
colorTo: cyan
sdk: docker
app_port: 7860
---
```

After the `*.hf.space` URL is healthy, configure the custom domain in the Space settings and replace the Google DNS records with the records Hugging Face shows.

## Notes

- Do not set `FORECAST_CACHE_BUCKET` on free non-Google hosting. Without it, the app uses in-memory/runtime caches only and avoids Google Cloud Storage.
- The sounding plot is rendered inside Model Forecast. It does not require publishing or calling `soundingscopepy.app`.
- Severe composites on Render are served from precomputed artifacts. See `docs/artifact_pipeline.md` for the refresh schedule, the verifier script, and production smoke-test URLs (including `/api/artifacts/status`).
