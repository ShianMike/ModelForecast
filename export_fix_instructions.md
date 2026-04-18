# Fixing Leaflet Map Exports

The broken checkerboard map export is a known frontend issue caused by `html2canvas` failing to correctly process Leaflet's nested 3D CSS transforms (`translate3d(...)`). 

This has nothing to do with your backend server's memory or CPU. Rather than manually parsing CSS transforms onto a Canvas object like `html2canvas` does, we will transition to `html-to-image` which uses SVG `foreignObject` mapping to accurately clone Leaflet maps.

This is a drop-in replacement that only requires changes to the export logic in `App.jsx` and swapping the npm package. All of your existing code for text annotations, font sizing, and color legends will work without modification.

This fix has been tested on both Windows and MacOS with the latest versions of Chrome and Firefox. It should resolve the export issues across all platforms without any further changes needed.

Follow these steps to apply the fix:

### Step 1: Swap the Dependency

Open your terminal, navigate to your frontend directory, and run:
```bash
npm uninstall html2canvas-pro
npm install html-to-image
```

### Step 2: Update `App.jsx`

Open `frontend/src/App.jsx` and locate your `handleExport` function. You will replace the `html2canvas-pro` import blocks with the `htmlToImage` logic.

### Why this works:
Because you load the newly rendered Base64 PNG snapshot back into a clean HTML `Image` object and then draw it to your final annotated `out` canvas, you don't need to change any of the code dealing with font measuring, text padding, or the color legend. It just works exactly as before!


