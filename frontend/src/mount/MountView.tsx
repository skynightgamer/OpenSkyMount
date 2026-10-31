// 3D visualisation of the simulated alt-az mount, driven by live telemetry.
// World frame (three.js, Y up): x = East, y = Up, z = South (North = -z).
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { getState, useStore } from "../store";

const DEG = Math.PI / 180;

function textSprite(text: string, color: string): THREE.Sprite {
  const c = document.createElement("canvas");
  c.width = 128;
  c.height = 128;
  const g = c.getContext("2d")!;
  g.fillStyle = color;
  g.font = "bold 72px system-ui, sans-serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText(text, 64, 64);
  const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(c), depthTest: false }));
  sprite.scale.set(0.35, 0.35, 0.35);
  return sprite;
}

function buildPayload(shape: string): THREE.Group {
  const g = new THREE.Group();
  const body = new THREE.MeshStandardMaterial({ color: 0x2b3240, metalness: 0.4, roughness: 0.5 });
  const accent = new THREE.MeshStandardMaterial({ color: 0x111418, metalness: 0.2, roughness: 0.3 });
  const glass = new THREE.MeshStandardMaterial({ color: 0x3d6bd6, metalness: 0.9, roughness: 0.1 });
  // Payload optical axis points along local -z.
  const add = (geo: THREE.BufferGeometry, mat: THREE.Material, z: number, rotX = false) => {
    const m = new THREE.Mesh(geo, mat);
    if (rotX) m.rotation.x = Math.PI / 2;
    m.position.z = z;
    m.castShadow = true;
    g.add(m);
    return m;
  };
  switch (shape) {
    case "telescope": {
      add(new THREE.CylinderGeometry(0.07, 0.07, 0.75, 32), new THREE.MeshStandardMaterial({ color: 0xe8ecf2, roughness: 0.4 }), -0.05, true);
      add(new THREE.CylinderGeometry(0.075, 0.075, 0.14, 32), accent, -0.42, true);
      add(new THREE.CylinderGeometry(0.02, 0.02, 0.12, 16), accent, 0.36, true);
      break;
    }
    case "telephoto": {
      add(new THREE.BoxGeometry(0.16, 0.12, 0.1), body, 0.12);
      add(new THREE.CylinderGeometry(0.055, 0.045, 0.42, 32), new THREE.MeshStandardMaterial({ color: 0xd9d6cc, roughness: 0.5 }), -0.15, true);
      add(new THREE.CylinderGeometry(0.056, 0.056, 0.02, 32), glass, -0.37, true);
      break;
    }
    case "dslr":
    case "camera": {
      const s = shape === "dslr" ? 1 : 0.75;
      add(new THREE.BoxGeometry(0.16 * s, 0.11 * s, 0.08 * s), body, 0.03);
      add(new THREE.CylinderGeometry(0.04 * s, 0.04 * s, 0.1 * s, 32), accent, -0.06 * s, true);
      add(new THREE.CylinderGeometry(0.035 * s, 0.035 * s, 0.01, 32), glass, -0.115 * s, true);
      break;
    }
    default: {
      // Smartphone: thin slab, camera on the back facing along -z.
      add(new THREE.BoxGeometry(0.085, 0.17, 0.012), accent, 0);
      add(new THREE.CylinderGeometry(0.012, 0.012, 0.006, 24), glass, -0.008, true).position.set(0.02, 0.06, -0.008);
      // Holder clamp.
      add(new THREE.BoxGeometry(0.11, 0.03, 0.03), body, 0.012).position.set(0, 0, 0.012);
    }
  }
  return g;
}

export default function MountView() {
  const wrapRef = useRef<HTMLDivElement>(null);
  const payloadShape = useStore((s) => {
    const c = s.config;
    return c ? c.payloads[c.config.payload]?.shape ?? "phone" : "phone";
  });
  const payloadRef = useRef<{ holder: THREE.Group | null; shape: string }>({ holder: null, shape: "" });

  useEffect(() => {
    const wrap = wrapRef.current!;
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.shadowMap.enabled = true;
    wrap.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x070a12);
    const camera = new THREE.PerspectiveCamera(40, 1, 0.05, 100);
    camera.position.set(1.05, 1.15, 1.35);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0.68, 0);
    controls.enableDamping = true;
    controls.maxPolarAngle = Math.PI * 0.55;
    controls.minDistance = 0.8;
    controls.maxDistance = 8;

    scene.add(new THREE.HemisphereLight(0x8aa4d6, 0x1a1410, 0.9));
    const sun = new THREE.DirectionalLight(0xffffff, 1.4);
    sun.position.set(2, 4, 1.5);
    sun.castShadow = true;
    scene.add(sun);

    // Ground with compass rose.
    const ground = new THREE.Mesh(new THREE.CircleGeometry(3, 64), new THREE.MeshStandardMaterial({ color: 0x141922, roughness: 1 }));
    ground.rotation.x = -Math.PI / 2;
    ground.receiveShadow = true;
    scene.add(ground);
    const ring = new THREE.Mesh(new THREE.RingGeometry(1.15, 1.17, 96), new THREE.MeshBasicMaterial({ color: 0x334055 }));
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.002;
    scene.add(ring);
    const dirs: [string, number, number, string][] = [
      ["N", 0, -1.35, "#ff6b6b"],
      ["E", 1.35, 0, "#c8d2e4"],
      ["S", 0, 1.35, "#c8d2e4"],
      ["W", -1.35, 0, "#c8d2e4"],
    ];
    for (const [t, x, z, col] of dirs) {
      const s = textSprite(t, col);
      s.position.set(x, 0.05, z);
      scene.add(s);
    }

    const metal = new THREE.MeshStandardMaterial({ color: 0x9aa6b8, metalness: 0.7, roughness: 0.35 });
    const dark = new THREE.MeshStandardMaterial({ color: 0x232a36, metalness: 0.5, roughness: 0.5 });
    // Tripod.
    for (let i = 0; i < 3; i++) {
      const a = (i * 2 * Math.PI) / 3 + Math.PI / 6;
      const top = new THREE.Vector3(Math.cos(a) * 0.06, 0.42, Math.sin(a) * 0.06);
      const foot = new THREE.Vector3(Math.cos(a) * 0.42, 0, Math.sin(a) * 0.42);
      const len = top.distanceTo(foot);
      const leg = new THREE.Mesh(new THREE.CylinderGeometry(0.014, 0.018, len, 12), metal);
      leg.position.copy(top).add(foot).multiplyScalar(0.5);
      leg.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), top.clone().sub(foot).normalize());
      leg.castShadow = true;
      scene.add(leg);
    }
    const head = new THREE.Mesh(new THREE.CylinderGeometry(0.09, 0.09, 0.05, 32), dark);
    head.position.y = 0.42;
    scene.add(head);
    const base = new THREE.Mesh(new THREE.CylinderGeometry(0.13, 0.14, 0.08, 48), dark);
    base.position.y = 0.48;
    base.castShadow = true;
    scene.add(base);

    // Azimuth stage (rotates about the vertical axis).
    const azGroup = new THREE.Group();
    azGroup.position.y = 0.52;
    scene.add(azGroup);
    const turntable = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.03, 48), metal);
    turntable.position.y = 0.015;
    azGroup.add(turntable);
    const marker = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.031, 0.05), new THREE.MeshStandardMaterial({ color: 0xff6b6b }));
    marker.position.set(0, 0.016, -0.1);
    azGroup.add(marker);
    for (const side of [-1, 1]) {
      const arm = new THREE.Mesh(new THREE.BoxGeometry(0.025, 0.3, 0.07), dark);
      arm.position.set(side * 0.13, 0.17, 0);
      arm.castShadow = true;
      azGroup.add(arm);
      const hub = new THREE.Mesh(new THREE.CylinderGeometry(0.035, 0.035, 0.03, 24), metal);
      hub.rotation.z = Math.PI / 2;
      hub.position.set(side * 0.13, 0.29, 0);
      azGroup.add(hub);
    }

    // Altitude stage (rotates about the horizontal East-West axis of the fork).
    const altGroup = new THREE.Group();
    altGroup.position.y = 0.29;
    azGroup.add(altGroup);
    const axle = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, 0.24, 16), metal);
    axle.rotation.z = Math.PI / 2;
    altGroup.add(axle);
    const holder = new THREE.Group();
    altGroup.add(holder);
    payloadRef.current.holder = holder;

    // Pointing ray (actual) and target direction.
    const rayMat = new THREE.LineBasicMaterial({ color: 0xff5b7a });
    const ray = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0, 0, 0), new THREE.Vector3(0, 0, -2.2)]), rayMat);
    altGroup.add(ray);
    const targetGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
    const targetLine = new THREE.Line(targetGeo, new THREE.LineDashedMaterial({ color: 0xffb347, dashSize: 0.06, gapSize: 0.04 }));
    scene.add(targetLine);
    const pivot = new THREE.Vector3(0, 0.81, 0);

    const resize = () => {
      const w = wrap.clientWidth;
      const h = wrap.clientHeight;
      renderer.setSize(w, h, false);
      renderer.domElement.style.width = `${w}px`;
      renderer.domElement.style.height = `${h}px`;
      camera.aspect = w / Math.max(1, h);
      camera.updateProjectionMatrix();
    };
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);
    resize();

    let raf = 0;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      const m = getState().mount;
      if (m) {
        // Physical axis angles (simulation ground truth when available).
        const altAxis = m.driver.truth?.alt_axis ?? m.position.alt_axis;
        const azAxis = m.driver.truth?.az_axis ?? m.position.az_axis;
        azGroup.rotation.y = -azAxis * DEG;
        altGroup.rotation.x = altAxis * DEG;
        if (m.target) {
          const a = m.target.alt * DEG;
          const z = m.target.az * DEG;
          const dir = new THREE.Vector3(Math.cos(a) * Math.sin(z), Math.sin(a), -Math.cos(a) * Math.cos(z));
          targetGeo.setFromPoints([pivot, pivot.clone().addScaledVector(dir, 2.2)]);
          targetLine.computeLineDistances();
          targetLine.visible = true;
        } else targetLine.visible = false;
      }
      controls.update();
      renderer.render(scene, camera);
    };
    loop();
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      controls.dispose();
      renderer.dispose();
      wrap.removeChild(renderer.domElement);
    };
  }, []);

  useEffect(() => {
    const ref = payloadRef.current;
    if (!ref.holder || ref.shape === payloadShape) return;
    ref.holder.clear();
    ref.holder.add(buildPayload(payloadShape));
    ref.shape = payloadShape;
  });

  return (
    <div className="mount-wrap" ref={wrapRef}>
      <div className="mount-legend">
        <span className="dot red" /> payload axis <span className="dot amber" /> target direction
      </div>
    </div>
  );
}
