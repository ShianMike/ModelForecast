# Fixing Leaflet Map Exports

The broken checkerboard map export is a known frontend issue caused by `html2canvas` failing to correctly process Leaflet's nested 3D CSS transforms (`translate3d(...)`). 

This has nothing to do with your backend server's memory or CPU. Rather than manually parsing CSS transforms onto a Canvas object like `html2canvas` does, we will transition to `html-to-image` which uses SVG `foreignObject` mapping to accurately clone Leaflet maps.

Follow these steps to apply the fix:

### Step 1: Swap the Dependency

Open your terminal, navigate to your frontend directory, and run:
```bash
npm uninstall html2canvas-pro
npm install html-to-image
```

### Step 2: Update `App.jsx`

Open `frontend/src/App.jsx` and locate your `handleExport` function. You will replace the `html2canvas-pro` import blocks with the `htmlToImage` logic.

Copy and paste this perfectly over the top section of `handleExport`:

```javascript
  const handleExport = useCallback(async () => {
    const el = document.querySelector(".forecast-map");
    if (!el) return;
    
    try {
      /* 1. Import the new library */
      const htmlToImage = await import("html-to-image");
      
      /* 2. Capture the DOM to a Base64 PNG. 
         This perfectly serializes Leaflet's 3D transforms. */
      const dataUrl = await htmlToImage.toPng(el, {
        pixelRatio: 2,
        backgroundColor: "#000" // Prevents transparent map backgrounds
      });

      /* 3. Load the PNG into an Image object so it can be annotated */
      const mapImg = new Image();
      mapImg.src = dataUrl;
      await new Promise(r => mapImg.onload = r);

      const W = mapImg.width;
      const H = mapImg.height;
      const PAD = 16;
      const scale = W / el.offsetWidth; /* retina scale factor */

      /* 4. Compose a new canvas for the exact weather overlays */
      const out = document.createElement("canvas");
      out.width = W;
      out.height = H;
      const ctx = out.getContext("2d");

      /* 5. Draw the correctly rendered map screenshot */
      ctx.drawImage(mapImg, 0, 0);

      /* ── Top-left: parameter name ────────────────────────── */
      // ... keep the rest of your amazing annotation/text drawing exactly the same!
      const titleText = `${paramInfo.name}${paramInfo.unit ? ` (${paramInfo.unit})` : ""}`;
```

### Why this works:
Because you load the newly rendered Base64 PNG snapshot back into a clean HTML `Image` object and then draw it to your final annotated `out` canvas, you don't need to change any of the code dealing with font measuring, text padding, or the color legend. It just works exactly as before!
