const PANO_VERTEX = `
attribute vec2 aPosition;
varying vec2 vNdc;
void main() {
  vNdc = aPosition;
  gl_Position = vec4(aPosition, 0.0, 1.0);
}`;

const PANO_FRAGMENT = `
precision highp float;
varying vec2 vNdc;
uniform samplerCube uCube;
uniform vec3 uForward;
uniform vec3 uRight;
uniform vec3 uUp;
uniform float uAspect;
uniform float uTanHalfFov;
uniform float uShowSeams;

void main() {
  vec3 ray = normalize(uForward + uRight * (vNdc.x * uAspect * uTanHalfFov) + uUp * (vNdc.y * uTanHalfFov));
  vec3 color = textureCube(uCube, ray).rgb;
  vec3 a = abs(ray);
  float largest = max(a.x, max(a.y, a.z));
  float smallest = min(a.x, min(a.y, a.z));
  float middle = a.x + a.y + a.z - largest - smallest;
  float edge = 1.0 - smoothstep(0.0015, 0.006, largest - middle);
  color = mix(color, vec3(1.0, 0.56, 0.27), edge * uShowSeams * 0.62);
  float vignette = 1.0 - 0.18 * pow(length(vNdc) * 0.72, 2.2);
  gl_FragColor = vec4(color * vignette, 1.0);
}`;

const CUBE_VERTEX = `
attribute vec3 aPosition;
uniform mat4 uMvp;
varying vec3 vDirection;
void main() {
  vDirection = aPosition;
  gl_Position = uMvp * vec4(aPosition, 1.0);
}`;

const CUBE_FRAGMENT = `
precision highp float;
varying vec3 vDirection;
uniform samplerCube uCube;
uniform float uShowSeams;

void main() {
  vec3 direction = normalize(vDirection);
  vec3 color = textureCube(uCube, direction).rgb;
  vec3 a = abs(direction);
  float largest = max(a.x, max(a.y, a.z));
  float smallest = min(a.x, min(a.y, a.z));
  float middle = a.x + a.y + a.z - largest - smallest;
  float edge = 1.0 - smoothstep(0.002, 0.009, largest - middle);
  color = mix(color, vec3(1.0, 0.56, 0.27), edge * uShowSeams * 0.78);
  float faceShade = 0.88 + 0.12 * largest;
  gl_FragColor = vec4(color * faceShade, 1.0);
}`;

const CUBE_VERTICES = new Float32Array([
  -1, -1, 1, 1, -1, 1, 1, 1, 1, -1, -1, 1, 1, 1, 1, -1, 1, 1,
  1, -1, 1, 1, -1, -1, 1, 1, -1, 1, -1, 1, 1, 1, -1, 1, 1, 1,
  1, -1, -1, -1, -1, -1, -1, 1, -1, 1, -1, -1, -1, 1, -1, 1, 1, -1,
  -1, -1, -1, -1, -1, 1, -1, 1, 1, -1, -1, -1, -1, 1, 1, -1, 1, -1,
  -1, 1, 1, 1, 1, 1, 1, 1, -1, -1, 1, 1, 1, 1, -1, -1, 1, -1,
  -1, -1, -1, 1, -1, -1, 1, -1, 1, -1, -1, -1, 1, -1, 1, -1, -1, 1,
]);

function compile(gl, type, source) {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    throw new Error(gl.getShaderInfoLog(shader) || "Shader compilation failed");
  }
  return shader;
}

function createProgram(gl, vertexSource, fragmentSource) {
  const program = gl.createProgram();
  gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, vertexSource));
  gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, fragmentSource));
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    throw new Error(gl.getProgramInfoLog(program) || "Program linking failed");
  }
  return program;
}

function perspective(fovDegrees, aspect, near, far) {
  const f = 1 / Math.tan((fovDegrees * Math.PI / 180) / 2);
  return new Float32Array([
    f / aspect, 0, 0, 0,
    0, f, 0, 0,
    0, 0, (far + near) / (near - far), -1,
    0, 0, (2 * far * near) / (near - far), 0,
  ]);
}

function normalize(vector) {
  const length = Math.hypot(...vector) || 1;
  return vector.map((value) => value / length);
}

function cross(a, b) {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

function lookAt(eye, target, up) {
  const z = normalize([eye[0] - target[0], eye[1] - target[1], eye[2] - target[2]]);
  const x = normalize(cross(up, z));
  const y = cross(z, x);
  return new Float32Array([
    x[0], y[0], z[0], 0,
    x[1], y[1], z[1], 0,
    x[2], y[2], z[2], 0,
    -x[0] * eye[0] - x[1] * eye[1] - x[2] * eye[2],
    -y[0] * eye[0] - y[1] * eye[1] - y[2] * eye[2],
    -z[0] * eye[0] - z[1] * eye[1] - z[2] * eye[2],
    1,
  ]);
}

function multiply(a, b) {
  const out = new Float32Array(16);
  for (let column = 0; column < 4; column += 1) {
    for (let row = 0; row < 4; row += 1) {
      out[column * 4 + row] =
        a[row] * b[column * 4]
        + a[4 + row] * b[column * 4 + 1]
        + a[8 + row] * b[column * 4 + 2]
        + a[12 + row] * b[column * 4 + 3];
    }
  }
  return out;
}

function rotationY(angle) {
  const c = Math.cos(angle);
  const s = Math.sin(angle);
  return new Float32Array([
    c, 0, -s, 0,
    0, 1, 0, 0,
    s, 0, c, 0,
    0, 0, 0, 1,
  ]);
}

export class CubeViewer {
  constructor(canvas) {
    this.canvas = canvas;
    this.gl = canvas.getContext("webgl2", { antialias: false, powerPreference: "high-performance" })
      || canvas.getContext("webgl", { antialias: false, powerPreference: "high-performance" });
    if (!this.gl) throw new Error("当前浏览器不支持 WebGL。");
    this.mode = "panorama";
    this.yaw = -0.4;
    this.pitch = 0.03;
    this.orbitYaw = 0.58;
    this.orbitPitch = 0.24;
    this.fov = 78;
    this.tens = 1;
    this.autoRotate = true;
    this.angularVelocity = 30;
    this.showSeams = false;
    this.dragging = false;
    this.lastPointer = null;
    this.init();
  }

  init() {
    const gl = this.gl;
    this.panoProgram = createProgram(gl, PANO_VERTEX, PANO_FRAGMENT);
    this.cubeProgram = createProgram(gl, CUBE_VERTEX, CUBE_FRAGMENT);
    this.panoBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.panoBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]), gl.STATIC_DRAW);
    this.cubeBuffer = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.cubeBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, CUBE_VERTICES, gl.STATIC_DRAW);
    this.texture = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_CUBE_MAP, this.texture);
    gl.texParameteri(gl.TEXTURE_CUBE_MAP, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_CUBE_MAP, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_CUBE_MAP, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_CUBE_MAP, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    this.panoUniforms = this.uniformMap(this.panoProgram, [
      "uCube", "uForward", "uRight", "uUp", "uAspect", "uTanHalfFov", "uShowSeams",
    ]);
    this.cubeUniforms = this.uniformMap(this.cubeProgram, ["uCube", "uMvp", "uShowSeams"]);
    this.attachPointerEvents();
    window.addEventListener("resize", () => this.resize());
  }

  uniformMap(program, names) {
    const map = {};
    for (const name of names) map[name] = this.gl.getUniformLocation(program, name);
    return map;
  }

  attachPointerEvents() {
    this.canvas.addEventListener("pointerdown", (event) => {
      this.dragging = true;
      this.lastPointer = [event.clientX, event.clientY];
      this.canvas.setPointerCapture(event.pointerId);
    });
    this.canvas.addEventListener("pointermove", (event) => {
      if (!this.dragging || !this.lastPointer) return;
      const dx = event.clientX - this.lastPointer[0];
      const dy = event.clientY - this.lastPointer[1];
      this.lastPointer = [event.clientX, event.clientY];
      if (this.mode === "panorama") {
        this.yaw -= dx * 0.0042;
        this.pitch = Math.max(-1.48, Math.min(1.48, this.pitch + dy * 0.0036));
      } else {
        this.orbitYaw -= dx * 0.006;
        this.orbitPitch = Math.max(-0.9, Math.min(1.1, this.orbitPitch + dy * 0.005));
      }
    });
    const stop = (event) => {
      this.dragging = false;
      this.lastPointer = null;
      if (event.pointerId !== undefined && this.canvas.hasPointerCapture(event.pointerId)) {
        this.canvas.releasePointerCapture(event.pointerId);
      }
    };
    this.canvas.addEventListener("pointerup", stop);
    this.canvas.addEventListener("pointercancel", stop);
    this.canvas.addEventListener("wheel", (event) => {
      event.preventDefault();
      const factor = Math.exp(event.deltaY * 0.00075);
      this.fov = Math.max(34, Math.min(106, this.fov * factor));
    }, { passive: false });
  }

  setFaces(faceBytes, size) {
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_CUBE_MAP, this.texture);
    for (let face = 0; face < 6; face += 1) {
      gl.texImage2D(
        gl.TEXTURE_CUBE_MAP_POSITIVE_X + face,
        0,
        gl.RGBA,
        size,
        size,
        0,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        faceBytes[face],
      );
    }
  }

  setMode(mode) {
    this.mode = mode;
  }

  setAutoRotate(enabled) {
    this.autoRotate = enabled;
  }

  setAngularVelocity(value) {
    this.angularVelocity = value;
  }

  setSeams(visible) {
    this.showSeams = visible;
  }

  reset() {
    this.yaw = -0.4;
    this.pitch = 0.03;
    this.orbitYaw = 0.58;
    this.orbitPitch = 0.24;
    this.fov = 78;
  }

  update(deltaSeconds) {
    this.advance(deltaSeconds);
    this.draw();
  }

  advance(deltaSeconds) {
    if (this.autoRotate) {
      const radians = this.angularVelocity * Math.PI / 180 * deltaSeconds;
      if (this.mode === "panorama") this.yaw += radians;
      else this.orbitYaw += radians;
    }
  }

  resize() {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = Math.max(1, Math.floor(this.canvas.clientWidth * ratio));
    const height = Math.max(1, Math.floor(this.canvas.clientHeight * ratio));
    if (this.canvas.width !== width || this.canvas.height !== height) {
      this.canvas.width = width;
      this.canvas.height = height;
    }
  }

  draw() {
    this.resize();
    const gl = this.gl;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0.015, 0.025, 0.035, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    gl.activeTexture(gl.TEXTURE0);
    gl.bindTexture(gl.TEXTURE_CUBE_MAP, this.texture);
    if (this.mode === "cube") this.drawCube();
    else this.drawPanorama();
  }

  drawPanorama() {
    const gl = this.gl;
    const program = this.panoProgram;
    const aspect = this.canvas.width / this.canvas.height;
    const forward = [
      Math.cos(this.pitch) * Math.sin(this.yaw),
      Math.sin(this.pitch),
      Math.cos(this.pitch) * Math.cos(this.yaw),
    ];
    const right = [Math.cos(this.yaw), 0, -Math.sin(this.yaw)];
    const up = cross(forward, right);
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.panoBuffer);
    const location = gl.getAttribLocation(program, "aPosition");
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, 2, gl.FLOAT, false, 0, 0);
    gl.uniform1i(this.panoUniforms.uCube, 0);
    gl.uniform3fv(this.panoUniforms.uForward, forward);
    gl.uniform3fv(this.panoUniforms.uRight, right);
    gl.uniform3fv(this.panoUniforms.uUp, up);
    gl.uniform1f(this.panoUniforms.uAspect, aspect);
    gl.uniform1f(this.panoUniforms.uTanHalfFov, Math.tan(this.fov * Math.PI / 360));
    gl.uniform1f(this.panoUniforms.uShowSeams, this.showSeams ? 1 : 0);
    gl.disable(gl.DEPTH_TEST);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }

  drawCube() {
    const gl = this.gl;
    const program = this.cubeProgram;
    const radius = 5.1;
    const eye = [
      radius * Math.sin(this.orbitYaw) * Math.cos(this.orbitPitch),
      radius * Math.sin(this.orbitPitch),
      radius * Math.cos(this.orbitYaw) * Math.cos(this.orbitPitch),
    ];
    const aspect = this.canvas.width / this.canvas.height;
    const projection = perspective(40, aspect, 0.1, 20);
    const view = lookAt(eye, [0, 0, 0], [0, 1, 0]);
    const model = rotationY(-0.32);
    const mvp = multiply(projection, multiply(view, model));
    gl.useProgram(program);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.cubeBuffer);
    const location = gl.getAttribLocation(program, "aPosition");
    gl.enableVertexAttribArray(location);
    gl.vertexAttribPointer(location, 3, gl.FLOAT, false, 0, 0);
    gl.uniform1i(this.cubeUniforms.uCube, 0);
    gl.uniformMatrix4fv(this.cubeUniforms.uMvp, false, mvp);
    gl.uniform1f(this.cubeUniforms.uShowSeams, this.showSeams ? 1 : 0);
    gl.enable(gl.DEPTH_TEST);
    gl.disable(gl.CULL_FACE);
    gl.drawArrays(gl.TRIANGLES, 0, 36);
  }

  orientation() {
    return {
      yaw: ((this.yaw * 180 / Math.PI) % 360 + 360) % 360,
      pitch: this.pitch * 180 / Math.PI,
      fov: this.fov,
    };
  }

  glVersion() {
    return this.gl instanceof WebGL2RenderingContext ? "WEBGL 2" : "WEBGL 1";
  }
}
