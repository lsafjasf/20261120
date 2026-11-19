import { convertEquirectToCube } from "./equirect-cube.js";
import { measureCubeSeams } from "./metrics.js";

self.onmessage = (event) => {
  try {
    const { width, height, data, faceSize } = event.data;
    const source = {
      width,
      height,
      data: new Uint8ClampedArray(data),
    };
    const started = performance.now();
    const faces = convertEquirectToCube(source, faceSize);
    const metrics = measureCubeSeams(faces, faceSize);
    const buffers = faces.map((face) => face.buffer);
    self.postMessage({
      ok: true,
      faceSize,
      metrics,
      conversionMs: performance.now() - started,
      faces: buffers,
    }, buffers);
  } catch (error) {
    self.postMessage({ ok: false, error: error.message || String(error) });
  }
};
