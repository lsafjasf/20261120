import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

export const INPUT_POINTER = 0;
export const CODE_POINTER = 131072;
export const OUTPUT_POINTER = 196608;

const defaultWasmPath = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "..",
  "dist",
  "g711.wasm",
);
const WASM_PREFIX = { mulaw: "mu", alaw: "a" };

export async function loadG711(wasmPath = defaultWasmPath) {
  const bytes = await readFile(wasmPath);
  const { instance } = await WebAssembly.instantiate(bytes, {});
  return new G711Wasm(instance);
}

export class G711Wasm {
  constructor(instance) {
    this.instance = instance;
    this.exports = instance.exports;
  }

  encodeSample(codec, sample) {
    return this.exports[`${WASM_PREFIX[codec]}_encode_sample`](sample);
  }

  decodeSample(codec, code) {
    return this.exports[`${WASM_PREFIX[codec]}_decode_sample`](code);
  }

  loopbackSample(codec, sample) {
    return this.exports[`${WASM_PREFIX[codec]}_loopback_sample`](sample);
  }

  encode(codec, samples) {
    this.#writePcm(samples, INPUT_POINTER);
    this.exports[`${WASM_PREFIX[codec]}_encode`](
      INPUT_POINTER,
      CODE_POINTER,
      samples.length,
    );
    return Uint8Array.from(
      new Uint8Array(this.memory.buffer, CODE_POINTER, samples.length),
    );
  }

  decode(codec, codes) {
    new Uint8Array(this.memory.buffer, CODE_POINTER, codes.length).set(codes);
    this.exports[`${WASM_PREFIX[codec]}_decode`](
      CODE_POINTER,
      OUTPUT_POINTER,
      codes.length,
    );
    return Int16Array.from(
      new Int16Array(this.memory.buffer, OUTPUT_POINTER, codes.length),
    );
  }

  loopback(codec, samples) {
    this.#writePcm(samples, INPUT_POINTER);
    this.exports[`${WASM_PREFIX[codec]}_loopback`](
      INPUT_POINTER,
      OUTPUT_POINTER,
      samples.length,
    );
    return Int16Array.from(
      new Int16Array(this.memory.buffer, OUTPUT_POINTER, samples.length),
    );
  }

  #writePcm(samples, pointer) {
    new Int16Array(this.memory.buffer, pointer, samples.length).set(samples);
  }

  get memory() {
    return this.exports.memory;
  }
}
