import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import wabtFactory from "wabt";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourcePath = path.join(root, "src", "g711.wat");
const outputDir = path.join(root, "dist");
const outputPath = path.join(outputDir, "g711.wasm");

const wabt = await wabtFactory();
const source = await readFile(sourcePath, "utf8");
const module = wabt.parseWat(sourcePath, source, {
  mutable_globals: false,
  sat_float_to_int: false,
  sign_extension: false,
  simd: false,
  threads: false,
  reference_types: false,
  multi_value: false,
  bulk_memory: false,
});
module.resolveNames();
module.validate();

const { buffer } = module.toBinary({
  canonicalize_lebs: true,
  relocatable: false,
  write_debug_names: false,
});
await mkdir(outputDir, { recursive: true });
await writeFile(outputPath, Buffer.from(buffer));
module.destroy();

console.log(`Built ${path.relative(root, outputPath)} (${buffer.byteLength} bytes)`);
