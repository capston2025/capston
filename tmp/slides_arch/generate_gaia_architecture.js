const fs = require("fs");
const path = require("path");
const PptxGenJS = require("pptxgenjs");
const {
  warnIfSlideHasOverlaps,
  warnIfSlideElementsOutOfBounds,
} = require("./pptxgenjs_helpers/layout");

const pptx = new PptxGenJS();
pptx.layout = "LAYOUT_WIDE";
pptx.author = "OpenAI Codex";
pptx.company = "GAIA";
pptx.subject = "GAIA Architecture";
pptx.title = "GAIA Architecture";
pptx.lang = "ko-KR";
pptx.theme = {
  headFontFace: "Pretendard",
  bodyFontFace: "Pretendard",
  lang: "ko-KR",
};

const slide = pptx.addSlide();
slide.background = { color: "04193F" };

const C = {
  bg: "04193F",
  card: "0C2455",
  card2: "0A214D",
  white: "F8FBFF",
  soft: "B7C5E5",
  line: "315FBC",
  blue: "58A6FF",
  orange: "FF9D57",
  green: "43E1B1",
  bar: "12346C",
};

function rgba(hex, transparency) {
  return { color: hex, transparency };
}

function addNativeIcon(kind, x, y, accent) {
  const line = { color: accent, pt: 1.2 };
  const fill = { color: accent, transparency: 100 };

  if (kind === "input") {
    slide.addShape(pptx._shapes.RECTANGLE, {
      x,
      y,
      w: 0.28,
      h: 0.36,
      line,
      fill,
    });
    slide.addShape(pptx._shapes.RIGHT_TRIANGLE, {
      x: x + 0.2,
      y,
      w: 0.08,
      h: 0.08,
      rotate: 180,
      line,
      fill,
    });
    slide.addShape(pptx._shapes.LINE, {
      x: x + 0.06,
      y: y + 0.11,
      w: 0.14,
      h: 0,
      line,
    });
    slide.addShape(pptx._shapes.LINE, {
      x: x + 0.06,
      y: y + 0.17,
      w: 0.14,
      h: 0,
      line,
    });
    slide.addShape(pptx._shapes.LINE, {
      x: x + 0.06,
      y: y + 0.23,
      w: 0.1,
      h: 0,
      line,
    });
    return;
  }

  if (kind === "agent") {
    slide.addShape(pptx._shapes.ROUNDED_RECTANGLE, {
      x,
      y: y + 0.02,
      w: 0.28,
      h: 0.28,
      rectRadius: 0.03,
      line,
      fill,
    });
    [
      [x + 0.065, y + 0.085],
      [x + 0.16, y + 0.085],
      [x + 0.065, y + 0.18],
      [x + 0.16, y + 0.18],
    ].forEach(([sx, sy]) => {
      slide.addShape(pptx._shapes.RECTANGLE, {
        x: sx,
        y: sy,
        w: 0.045,
        h: 0.045,
        line,
        fill,
      });
    });
    return;
  }

  if (kind === "execution") {
    slide.addShape(pptx._shapes.ROUNDED_RECTANGLE, {
      x,
      y: y + 0.04,
      w: 0.3,
      h: 0.22,
      rectRadius: 0.03,
      line,
      fill,
    });
    slide.addShape(pptx._shapes.LINE, {
      x,
      y: y + 0.1,
      w: 0.3,
      h: 0,
      line,
    });
    slide.addShape(pptx._shapes.OVAL, {
      x: x + 0.04,
      y: y + 0.06,
      w: 0.02,
      h: 0.02,
      line: { color: accent, transparency: 100 },
      fill: { color: accent },
    });
    slide.addShape(pptx._shapes.OVAL, {
      x: x + 0.075,
      y: y + 0.06,
      w: 0.02,
      h: 0.02,
      line: { color: accent, transparency: 100 },
      fill: { color: accent },
    });
    slide.addShape(pptx._shapes.OVAL, {
      x: x + 0.11,
      y: y + 0.06,
      w: 0.02,
      h: 0.02,
      line: { color: accent, transparency: 100 },
      fill: { color: accent },
    });
    return;
  }

  slide.addShape(pptx._shapes.OVAL, {
    x: x + 0.01,
    y: y + 0.02,
    w: 0.28,
    h: 0.28,
    line,
    fill,
  });
  slide.addShape(pptx._shapes.LINE, {
    x: x + 0.08,
    y: y + 0.17,
    w: 0.055,
    h: 0.055,
    line,
  });
  slide.addShape(pptx._shapes.LINE, {
    x: x + 0.13,
    y: y + 0.225,
    w: 0.09,
    h: -0.11,
    line,
  });
}

function addGlow(x, y, w, h, color, transparency) {
  slide.addShape(pptx._shapes.OVAL, {
    x,
    y,
    w,
    h,
    line: { color, transparency: 100 },
    fill: rgba(color, transparency),
  });
}

function addArrow(x, y, w, h, color) {
  slide.addShape(pptx._shapes.RIGHT_ARROW, {
    x,
    y,
    w,
    h,
    line: { color, transparency: 100 },
    fill: { color },
  });
}

function addPill(text, x, y, w, h, fill, line, fontSize) {
  slide.addShape(pptx._shapes.ROUNDED_RECTANGLE, {
    x,
    y,
    w,
    h,
    rectRadius: 0.08,
    line: { color: line, pt: 1.4 },
    fill: { color: fill },
  });
  slide.addText(text, {
    x,
    y: y + 0.02,
    w,
    h,
    fontFace: "Pretendard",
    fontSize,
    color: C.white,
    align: "center",
    bold: true,
    margin: 0,
    valign: "mid",
  });
}

function addNumberBadge(num, x, y, color) {
  slide.addShape(pptx._shapes.OVAL, {
    x,
    y,
    w: 0.28,
    h: 0.28,
    line: { color, pt: 0.8 },
    fill: { color },
  });
  slide.addText(String(num), {
    x,
    y: y + 0.003,
    w: 0.28,
    h: 0.28,
    fontFace: "Pretendard",
    fontSize: 11,
    bold: true,
    align: "center",
    valign: "mid",
    color: "07152D",
    margin: 0,
  });
}

function addCard({
  x,
  y,
  w,
  h,
  title,
  lines,
  number,
  accent,
  icon,
  footer,
}) {
  slide.addShape(pptx._shapes.ROUNDED_RECTANGLE, {
    x,
    y,
    w,
    h,
    rectRadius: 0.08,
    line: { color: accent, pt: 1.2, transparency: 8 },
    fill: { color: C.card2 },
    shadow: {
      type: "outer",
      color: "00112E",
      blur: 2,
      angle: 45,
      distance: 1,
      opacity: 0.18,
    },
  });

  addNumberBadge(number, x + 0.14, y + 0.16, accent);

  slide.addShape(pptx._shapes.ROUNDED_RECTANGLE, {
    x: x + 0.16,
    y: y + 0.43,
    w: 0.62,
    h: 0.62,
    line: { color: accent, pt: 1.2 },
    fill: { color: accent, transparency: 85 },
  });

  addNativeIcon(icon, x + 0.305, y + 0.525, accent);

  slide.addText(title, {
    x: x + 0.9,
    y: y + 0.44,
    w: w - 1.1,
    h: 0.34,
    fontFace: "Pretendard",
    fontSize: 18,
    bold: true,
    color: C.white,
    margin: 0,
  });

  let cy = y + 0.98;
  for (const line of lines) {
    slide.addText(line, {
      x: x + 0.22,
      y: cy,
      w: w - 0.44,
      h: 0.25,
      fontFace: "Pretendard",
      fontSize: 9.5,
      color: C.soft,
      margin: 0,
    });
    cy += 0.29;
  }

  if (footer) {
    addPill(footer, x + 0.2, y + h - 0.46, w - 0.4, 0.28, accent, accent, 8.5);
  }
}

addGlow(-1.2, -0.9, 4.7, 4.7, "0F4FD6", 88);
addGlow(10.9, -1.0, 3.6, 3.6, "0E3AA8", 90);
addGlow(-0.4, 5.7, 3.0, 3.0, "0A38A6", 93);

slide.addText("GAIA 아키텍처", {
  x: 0.65,
  y: 0.38,
  w: 4.2,
  h: 0.5,
  fontFace: "Pretendard",
  fontSize: 28,
  bold: true,
  color: C.white,
  margin: 0,
});

slide.addText("LLM은 선택을, MCP Host + Playwright는 실행을, Validation은 검증과 복구를 담당합니다.", {
  x: 0.67,
  y: 0.87,
  w: 6.8,
  h: 0.22,
  fontFace: "Pretendard",
  fontSize: 10.2,
  color: C.soft,
  margin: 0,
});

addPill("LLM은 element_id를 선택", 4.45, 1.28, 2.85, 0.34, C.bar, C.line, 9.5);

addCard({
  x: 0.64,
  y: 1.92,
  w: 2.25,
  h: 2.48,
  title: "입력",
  lines: [
    "CLI / GUI / Chat / PRD Bundle",
    "사용자 목표와 기획서가 여기서 들어옵니다.",
  ],
  number: 1,
  accent: C.blue,
  icon: "input",
});

addCard({
  x: 3.16,
  y: 1.92,
  w: 2.75,
  h: 2.48,
  title: "Agent Layer",
  lines: [
    "GoalDrivenAgent / ExploratoryAgent",
    "phase · history · feedback · memory 관리",
    "실행 직전에 element_id -> ref_id 해석",
  ],
  number: 2,
  accent: C.blue,
  icon: "agent",
});

addCard({
  x: 6.2,
  y: 1.92,
  w: 3.18,
  h: 2.48,
  title: "MCP Host + Playwright",
  lines: [
    "browser_snapshot / browser_act 처리",
    "DOM / frame / state 수집과 실제 액션 수행",
    "snapshot 생성, ref_id 부여, stale / scope 검증",
  ],
  number: 3,
  accent: C.orange,
  icon: "execution",
  footer: "실행 계약: snapshot_id + ref_id",
});

addCard({
  x: 9.7,
  y: 1.92,
  w: 2.97,
  h: 2.48,
  title: "Validation",
  lines: [
    "state_change / reason_code",
    "retry / resnapshot / context shift",
    "summary / artifacts / report",
  ],
  number: 4,
  accent: C.green,
  icon: "validation",
});

addArrow(2.92, 2.88, 0.42, 0.24, C.blue);
addArrow(5.95, 2.88, 0.42, 0.24, C.blue);
addArrow(9.44, 2.88, 0.42, 0.24, C.green);

slide.addText("LLM 출력: action + element_id", {
  x: 4.85,
  y: 1.63,
  w: 2.7,
  h: 0.18,
  fontFace: "Pretendard",
  fontSize: 8.2,
  color: C.soft,
  italic: true,
  align: "center",
  margin: 0,
});

slide.addShape(pptx._shapes.ROUNDED_RECTANGLE, {
  x: 1.15,
  y: 5.78,
  w: 11.03,
  h: 0.52,
  rectRadius: 0.08,
  line: { color: C.line, pt: 1.2 },
  fill: { color: C.bar, transparency: 8 },
});

slide.addText("핵심: LLM은 화면 번호표인 element_id를 고르고, 실제 실행은 snapshot_id + ref_id로 수행", {
  x: 1.32,
  y: 5.91,
  w: 10.68,
  h: 0.16,
  fontFace: "Pretendard",
  fontSize: 10,
  color: C.white,
  bold: true,
  align: "center",
  margin: 0,
});

if (process.env.SLIDE_DEBUG === "1") {
  warnIfSlideHasOverlaps(slide, pptx, {
    muteContainment: true,
    ignoreDecorativeShapes: true,
  });
  warnIfSlideElementsOutOfBounds(slide, pptx);
}

const outDir = path.resolve(__dirname, "../../output/ppt");
fs.mkdirSync(outDir, { recursive: true });

const outPath = path.join(outDir, "gaia_architecture_editable.pptx");
const jsOutPath = path.join(outDir, "gaia_architecture_editable.js");
fs.copyFileSync(__filename, jsOutPath);

pptx.writeFile({ fileName: outPath }).then(() => {
  console.log(`Wrote ${outPath}`);
  console.log(`Copied source to ${jsOutPath}`);
});
