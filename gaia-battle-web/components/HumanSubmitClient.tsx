"use client";

import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudUploadOutlined,
  DashboardOutlined,
  DeleteOutlined,
  FieldTimeOutlined,
  LinkOutlined,
  LockOutlined,
  PictureOutlined,
  PhoneOutlined,
  RobotOutlined,
  StopOutlined,
  UserOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Form,
  Image,
  Input,
  Row,
  Segmented,
  Space,
  Spin,
  Tag,
} from "antd";
import type { ClipboardEvent } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { isDemoLikeRecord } from "@/lib/cases";
import { BattleRecord, BattleSessionState, BattleStorageMode } from "@/lib/types";
import { useBattleRealtime, type BattleRealtimeStatus } from "./useBattleRealtime";

type Props = {
  sessionId: string;
  scenarioId: string;
  initialRecords: BattleRecord[];
};

type EvidenceImage = {
  dataUrl: string;
  name: string;
  size: number;
  width: number;
  height: number;
};

const STALE_HUMAN_RUN_MS = 20 * 60 * 1000;
const PREPARED_HUMAN_STARTED_AT_MS = Date.UTC(1970, 0, 1);
const MAX_EVIDENCE_DATA_URL_LENGTH = 320_000;
const EVIDENCE_START_LONG_SIDE = 900;
const EVIDENCE_MIN_LONG_SIDE = 420;
const MIN_SUCCESS_REASON_LENGTH = 20;
const MAX_EVIDENCE_IMAGES = 4;

function participantId(name: string) {
  return name.trim().toLowerCase().replace(/[^a-z0-9가-힣_-]+/gi, "-") || "human";
}

function seconds(value: number | null) {
  if (typeof value !== "number") return "-";
  return `${value.toFixed(value >= 10 ? 1 : 2)}s`;
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "-";
  return new Intl.DateTimeFormat("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    timeZone: "Asia/Seoul",
  }).format(date);
}

function metadataText(value: unknown) {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function isPublicEvidenceUrl(value: string) {
  return /^https?:\/\//i.test(value) || value.startsWith("/");
}

function evidenceImageUrls(record: BattleRecord) {
  const urls: string[] = [];
  const add = (value: string) => {
    if (!value || urls.includes(value)) return;
    if (value.startsWith("data:image/")) {
      urls.push(value);
      return;
    }
    if (isPublicEvidenceUrl(value) && /\.(png|jpe?g|webp|gif)(\?|#|$)/i.test(value)) {
      urls.push(value);
    }
  };
  const images = record.metadata?.evidenceImages;
  if (Array.isArray(images)) {
    for (const image of images) {
      if (image && typeof image === "object" && !Array.isArray(image)) {
        add(metadataText((image as Record<string, unknown>).dataUrl || (image as Record<string, unknown>).url));
      }
    }
  }
  add(metadataText(record.metadata?.evidenceImageDataUrl || record.metadata?.screenshotDataUrl));
  const url = metadataText(record.metadata?.screenshotUrl || record.artifactUrl);
  add(url);
  return urls;
}

function evidenceLinkUrl(record: BattleRecord) {
  const url = metadataText(record.metadata?.screenshotUrl || record.artifactUrl);
  if (/^https?:\/\//i.test(url)) return url;
  return "";
}

function recordUpdatedAtMs(record: BattleRecord) {
  const parsed = new Date(record.updatedAt || record.createdAt).getTime();
  return Number.isNaN(parsed) ? 0 : parsed;
}

function isRecordAtOrAfter(record: BattleRecord, startMs: number | null) {
  if (!startMs) return false;
  return recordUpdatedAtMs(record) >= startMs;
}

function realtimeBadge(status: BattleRealtimeStatus) {
  if (status === "subscribed") return <Badge status="processing" text="실시간 수신" />;
  if (status === "connecting") return <Badge status="processing" text="연결 중" />;
  return <Badge status="default" text="동기화 중" />;
}

function terminalGaiaStatus(record: BattleRecord | null, realtimeStatus: BattleRealtimeStatus) {
  if (!record) return realtimeBadge(realtimeStatus);
  if (record.status === "RUNNING") return <Badge status="processing" text="실행 중" />;
  const color = record.status === "SUCCESS" ? "success" : record.status === "FAIL" ? "error" : "warning";
  return (
    <Space className="gaiaRunStatus" size={8}>
      <Badge status={record.status === "SUCCESS" ? "success" : "warning"} text="종료" />
      <Tag color={color} icon={<ClockCircleOutlined />}>
        {seconds(record.durationSeconds)}
      </Tag>
    </Space>
  );
}

function readFileAsDataUrl(file: File) {
  return new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("file read failed"));
    reader.readAsDataURL(file);
  });
}

function loadImage(src: string) {
  return new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new window.Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("image load failed"));
    image.src = src;
  });
}

async function compressScreenshot(file: File): Promise<EvidenceImage> {
  const rawDataUrl = await readFileAsDataUrl(file);
  const image = await loadImage(rawDataUrl);
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context) throw new Error("canvas unavailable");

  const naturalLongSide = Math.max(1, image.naturalWidth, image.naturalHeight);
  let longSide = Math.min(EVIDENCE_START_LONG_SIDE, naturalLongSide);
  const qualities = [0.72, 0.62, 0.52, 0.44, 0.36];
  let best: EvidenceImage | null = null;

  while (longSide >= EVIDENCE_MIN_LONG_SIDE) {
    const scale = Math.min(1, longSide / naturalLongSide);
    const width = Math.max(1, Math.round(image.naturalWidth * scale));
    const height = Math.max(1, Math.round(image.naturalHeight * scale));
    canvas.width = width;
    canvas.height = height;
    context.clearRect(0, 0, width, height);
    context.drawImage(image, 0, 0, width, height);

    for (const quality of qualities) {
      const dataUrl = canvas.toDataURL("image/jpeg", quality);
      const candidate = {
        dataUrl,
        name: file.name || "pasted-screenshot.jpg",
        size: dataUrl.length,
        width,
        height,
      };
      best = candidate;
      if (dataUrl.length <= MAX_EVIDENCE_DATA_URL_LENGTH) return candidate;
    }
    longSide = Math.floor(longSide * 0.78);
  }

  if (best && best.dataUrl.length <= MAX_EVIDENCE_DATA_URL_LENGTH * 1.2) return best;
  throw new Error("evidence image too large");
}

async function readJsonOrError(response: Response): Promise<{ record?: BattleRecord; records?: BattleRecord[]; error?: string }> {
  const text = await response.text();
  if (!text.trim()) return {};
  try {
    return JSON.parse(text) as { record?: BattleRecord; records?: BattleRecord[]; error?: string };
  } catch {
    return {
      error: text.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim().slice(0, 160) || "서버 응답 오류",
    };
  }
}

export function HumanSubmitClient({ sessionId, scenarioId, initialRecords }: Props) {
  const [name, setName] = useState("");
  const [phoneNumber, setPhoneNumber] = useState("");
  const [status, setStatus] = useState<"SUCCESS" | "FAIL">("SUCCESS");
  const [sessionStartedAt, setSessionStartedAt] = useState<string | null>(null);
  // The operator-visible "상황". The matching key (scenarioId) is received from
  // the session state and never shown to the participant.
  const [sessionScenarioId, setSessionScenarioId] = useState<string | null>(null);
  const [sessionScenarioLabel, setSessionScenarioLabel] = useState<string | null>(null);
  const [sessionUpdatedAt, setSessionUpdatedAt] = useState<string | null>(null);
  const [scenarioSituation, setScenarioSituation] = useState("");
  const [successReason, setSuccessReason] = useState("");
  const [evidenceImages, setEvidenceImages] = useState<EvidenceImage[]>([]);
  const [records, setRecords] = useState(initialRecords);
  const [message, setMessage] = useState<{ type: "success" | "info" | "warning" | "error"; text: string } | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [now, setNow] = useState(0);
  const [serverClockOffsetMs, setServerClockOffsetMs] = useState(0);
  const [storageMode, setStorageMode] = useState<BattleStorageMode>("memory");
  const [mounted, setMounted] = useState(false);
  const lastCaseResetKeyRef = useRef<string | null>(null);

  const effectiveScenarioId = sessionScenarioId || scenarioId;
  const sessionStartTime = useMemo(() => {
    if (!sessionStartedAt) return null;
    const parsed = new Date(sessionStartedAt).getTime();
    if (Number.isNaN(parsed)) return null;
    if (parsed <= PREPARED_HUMAN_STARTED_AT_MS) return null;
    return parsed;
  }, [sessionStartedAt]);
  const caseResetKey = useMemo(
    () => [effectiveScenarioId, sessionScenarioLabel || "", sessionUpdatedAt || ""].join("\u001f"),
    [effectiveScenarioId, sessionScenarioLabel, sessionUpdatedAt],
  );
  const submittedRecord = useMemo(() => {
    const id = participantId(name);
    if (!name.trim() || !sessionStartTime) return null;
    return (
      records.find(
        (record) =>
          record.participantType === "human" &&
          record.participantId === id &&
          record.scenarioId === effectiveScenarioId &&
          isRecordAtOrAfter(record, sessionStartTime),
      ) || null
    );
  }, [effectiveScenarioId, name, records, sessionStartTime]);
  const locked = Boolean(submittedRecord);
  const currentRunHasHumanRecord = useMemo(() => {
    if (!sessionStartTime) return false;
    return records.some(
      (record) =>
        record.participantType === "human" &&
        record.scenarioId === effectiveScenarioId &&
        !isDemoLikeRecord(record) &&
        isRecordAtOrAfter(record, sessionStartTime),
    );
  }, [effectiveScenarioId, records, sessionStartTime]);
  const displayNowMs = now;
  const staleRun = Boolean(
    sessionStartTime &&
      displayNowMs > 0 &&
      !submittedRecord &&
      !currentRunHasHumanRecord &&
      displayNowMs - sessionStartTime > STALE_HUMAN_RUN_MS,
  );
  const activeSessionStartTime = sessionStartTime && !currentRunHasHumanRecord && !staleRun ? sessionStartTime : null;
  const shouldShowCurrentRun = Boolean(activeSessionStartTime || submittedRecord);

  useEffect(() => {
    if (!caseResetKey) return;
    if (lastCaseResetKeyRef.current === null) {
      lastCaseResetKeyRef.current = caseResetKey;
      return;
    }
    if (lastCaseResetKeyRef.current === caseResetKey) return;
    lastCaseResetKeyRef.current = caseResetKey;
    if (sessionStartTime) return;
    const timer = window.setTimeout(() => {
      setName("");
      setPhoneNumber("");
      setStatus("SUCCESS");
      setSuccessReason("");
      setEvidenceImages([]);
      setMessage(null);
      setIsSubmitting(false);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [caseResetKey, sessionStartTime]);

  useEffect(() => {
    const timer = window.setTimeout(() => setMounted(true), 0);
    return () => window.clearTimeout(timer);
  }, []);

  const refreshSession = useCallback(async () => {
    const requestedAt = Date.now();
    const response = await fetch(`/api/session?sessionId=${encodeURIComponent(sessionId)}`, { cache: "no-store" });
    if (!response.ok) return;
    const receivedAt = Date.now();
    const data = (await response.json()) as {
      state: BattleSessionState | null;
      storage?: BattleStorageMode;
      serverNow?: string;
    };
    const serverNowMs = data.serverNow ? new Date(data.serverNow).getTime() : NaN;
    if (!Number.isNaN(serverNowMs)) {
      setServerClockOffsetMs(serverNowMs - Math.round((requestedAt + receivedAt) / 2));
      setNow(serverNowMs);
    }
    if (data.storage) setStorageMode(data.storage);
    setSessionStartedAt(data.state?.humanStartedAt || null);
    setSessionScenarioId(data.state?.scenarioId || null);
    setSessionScenarioLabel(data.state?.scenarioLabel || null);
    setSessionUpdatedAt(data.state?.updatedAt || null);
    if (data.state?.scenarioLabel) {
      setScenarioSituation(data.state.scenarioLabel);
    }
  }, [sessionId]);

  const refreshRecords = useCallback(async () => {
    const response = await fetch(`/api/records?sessionId=${encodeURIComponent(sessionId)}`, { cache: "no-store" });
    if (!response.ok) return;
    const data = (await response.json()) as { records: BattleRecord[]; storage?: BattleStorageMode };
    setRecords(data.records || []);
    if (data.storage) setStorageMode(data.storage);
  }, [sessionId]);

  const realtimeStatus = useBattleRealtime({
    sessionId,
    onRecordsChange: refreshRecords,
    onSessionChange: refreshSession,
  });
  const shouldPoll = realtimeStatus !== "subscribed";

  useEffect(() => {
    const initialTimer = window.setTimeout(() => void refreshSession(), 0);
    // Realtime can miss session-state changes when the state table is not part
    // of the active publication. Keep a tiny session heartbeat so selecting the
    // next demo case always resets the participant screen.
    const timer = window.setInterval(refreshSession, 500);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [refreshSession]);

  useEffect(() => {
    const initialTimer = window.setTimeout(() => void refreshRecords(), 0);
    if (!shouldPoll) return () => window.clearTimeout(initialTimer);
    const timer = window.setInterval(refreshRecords, 1500);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [refreshRecords, shouldPoll]);

  useEffect(() => {
    if (!activeSessionStartTime || locked) return;
    const timer = window.setInterval(() => setNow(Date.now() + serverClockOffsetMs), 100);
    return () => window.clearInterval(timer);
  }, [activeSessionStartTime, locked, serverClockOffsetMs]);

  const liveDuration =
    activeSessionStartTime && now ? Math.max(0, Math.round(((now - activeSessionStartTime) / 1000) * 100) / 100) : null;
  const submittedDuration =
    typeof submittedRecord?.durationSeconds === "number" ? submittedRecord.durationSeconds : null;
  const displayDuration = submittedDuration ?? liveDuration;
  const cleanSuccessReason = successReason.trim();
  const successReasonLength = cleanSuccessReason.length;
  const hasRequiredSuccessReason = successReasonLength >= MIN_SUCCESS_REASON_LENGTH;
  const hasRequiredScreenshot = evidenceImages.length > 0;
  const submitReady = Boolean(
    !locked &&
      !isSubmitting &&
      activeSessionStartTime &&
      name.trim() &&
      hasRequiredSuccessReason &&
      hasRequiredScreenshot,
  );
  const preparedScenario = Boolean(scenarioSituation.trim() && !activeSessionStartTime && !submittedRecord);
  const timerLabel = submittedRecord ? "제출 완료" : activeSessionStartTime ? "측정 중" : preparedScenario ? "준비 완료" : "시작 대기";
  const timerHelp = submittedRecord
    ? "타이머 종료"
    : activeSessionStartTime
      ? "타이머 자동 실행 중"
      : preparedScenario
        ? "시나리오 확인 후 시작 신호 대기"
        : "GAIA 시작 신호 대기";
  const gaiaRecords = useMemo(
    () => {
      if (storageMode === "memory" || !sessionStartTime || !shouldShowCurrentRun) return [];
      return records
        .filter(
          (record) =>
            record.participantType === "gaia" &&
            record.scenarioId === effectiveScenarioId &&
            !isDemoLikeRecord(record) &&
            isRecordAtOrAfter(record, sessionStartTime),
        )
        .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    },
    [effectiveScenarioId, records, sessionStartTime, shouldShowCurrentRun, storageMode],
  );
  const currentGaiaRecord = useMemo(() => {
    if (!sessionStartTime) return gaiaRecords[0] || null;
    return (
      gaiaRecords.find((record) => {
        const updatedAt = new Date(record.updatedAt).getTime();
        return !Number.isNaN(updatedAt) && updatedAt >= sessionStartTime;
      }) || null
    );
  }, [gaiaRecords, sessionStartTime]);

  if (!mounted) {
    return (
      <main className="appFrame humanFrame">
        <section className="appContainer humanContainer">
          <Card className="humanCard">
            <Tag color="blue" icon={<FieldTimeOutlined />}>
              Human QA
            </Tag>
            <div className="humanTimerHero">
              <span>시작 대기</span>
              <strong>
                <ClockCircleOutlined />
                0.00s
              </strong>
            </div>
          </Card>
        </section>
      </main>
    );
  }

  async function handleScreenshotPaste(event: ClipboardEvent<HTMLDivElement>) {
    if (isSubmitting) return;
    const imageItems = Array.from(event.clipboardData.items).filter((entry) => entry.type.startsWith("image/"));
    if (!imageItems.length) {
      setMessage({ type: "warning", text: "클립보드에 이미지가 없습니다." });
      return;
    }
    event.preventDefault();
    const remainingSlots = MAX_EVIDENCE_IMAGES - evidenceImages.length;
    if (remainingSlots <= 0) {
      setMessage({ type: "warning", text: `스크린샷은 최대 ${MAX_EVIDENCE_IMAGES}개까지 첨부할 수 있습니다.` });
      return;
    }
    const files = imageItems
      .slice(0, remainingSlots)
      .map((item) => item.getAsFile())
      .filter((file): file is File => Boolean(file));
    if (!files.length) {
      setMessage({ type: "error", text: "스크린샷을 읽지 못했습니다." });
      return;
    }
    const compressedImages: EvidenceImage[] = [];
    let failedCount = 0;
    for (const file of files) {
      try {
        compressedImages.push(await compressScreenshot(file));
      } catch {
        failedCount += 1;
      }
    }
    if (compressedImages.length) {
      setEvidenceImages((currentImages) => [...currentImages, ...compressedImages].slice(0, MAX_EVIDENCE_IMAGES));
      const ignoredCount = Math.max(0, imageItems.length - files.length);
      const suffix = ignoredCount ? ` ${ignoredCount}개는 최대 개수 제한으로 제외됐습니다.` : "";
      setMessage({ type: "success", text: `스크린샷 ${compressedImages.length}개가 붙었습니다.${suffix}` });
      return;
    }
    if (failedCount) {
      setMessage({ type: "error", text: "스크린샷이 너무 큽니다. 화면 일부만 캡처해서 다시 붙여주세요." });
    }
  }

  async function submit() {
    if (isSubmitting) return;
    const cleanName = name.trim();
    if (!cleanName) {
      setMessage({ type: "warning", text: "이름을 먼저 입력해줘." });
      return;
    }
    if (!activeSessionStartTime) {
      setMessage({ type: "warning", text: "아직 시작 신호가 없습니다." });
      return;
    }
    if (locked) {
      setMessage({ type: "warning", text: "이미 기록됨" });
      return;
    }
    if (cleanSuccessReason.length < MIN_SUCCESS_REASON_LENGTH) {
      setMessage({ type: "warning", text: `성공 이유를 ${MIN_SUCCESS_REASON_LENGTH}자 이상 입력해줘.` });
      return;
    }
    if (!evidenceImages.length) {
      setMessage({ type: "warning", text: "증거 스크린샷을 1개 붙여넣어줘." });
      return;
    }
    setIsSubmitting(true);
    setMessage({ type: "info", text: "제출 중입니다. 잠시만 기다려주세요." });
    try {
      const finalDuration = Math.max(0, Math.round((((now || Date.now()) - activeSessionStartTime) / 1000) * 100) / 100);
      const cleanSituation = scenarioSituation.trim();
      const cleanPhoneNumber = phoneNumber.trim();
      const response = await fetch("/api/records", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          sessionId,
          participantId: participantId(cleanName),
          participantName: cleanName,
          participantType: "human",
          scenarioId: effectiveScenarioId,
          scenarioLabel: cleanSituation || "Live QA Mission",
          status,
          durationSeconds: finalDuration,
          reason: cleanSuccessReason,
          artifactUrl: "",
          metadata: {
            evidenceSource: "human-form",
            rafflePhoneNumber: cleanPhoneNumber || undefined,
            sessionStartedAt,
            scenarioSituation: cleanSituation,
            successReason: cleanSuccessReason,
            evidenceImageDataUrl: evidenceImages[0]?.dataUrl || "",
            evidenceImageName: evidenceImages[0]?.name || "",
            evidenceImageSize: evidenceImages[0]?.size || 0,
            evidenceImageWidth: evidenceImages[0]?.width || 0,
            evidenceImageHeight: evidenceImages[0]?.height || 0,
            evidenceImageCount: evidenceImages.length,
            evidenceImages: evidenceImages.map((image, index) => ({
              dataUrl: image.dataUrl,
              name: image.name,
              size: image.size,
              width: image.width,
              height: image.height,
              index,
            })),
          },
        }),
      });
      const data = await readJsonOrError(response);
      if (!response.ok) {
        setMessage({ type: "error", text: data.error || "제출 실패" });
        return;
      }
      setRecords((currentRecords) => data.records || (data.record ? [data.record, ...currentRecords] : currentRecords));
      setMessage({ type: "success", text: "제출 완료. 타이머 종료." });
    } catch {
      setMessage({ type: "error", text: "제출 중 네트워크 오류가 발생했습니다." });
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className="appFrame humanFrame">
      <Spin description="제출 중" fullscreen spinning={isSubmitting} />
      <section className="appContainer humanContainer">
        <Card className="humanCard">
          <div className="humanHeader">
            <Space className="humanTimerHeader" orientation="vertical" size={10}>
              <Tag color="blue" icon={<FieldTimeOutlined />}>
                Human QA
              </Tag>
              <div className="humanTimerHero">
                <span>{timerLabel}</span>
                <strong>
                  <ClockCircleOutlined />
                  {seconds(displayDuration ?? 0)}
                </strong>
                <em>{timerHelp}</em>
              </div>
            </Space>
            <Button href={`/battle/${sessionId}`} icon={<DashboardOutlined />} size="large">
              점수판
            </Button>
          </div>
        </Card>

        <Row className="humanWorkspace" gutter={[16, 16]}>
          <Col xs={24} lg={15}>
            <Card
              className="humanInputCard"
              title={
                <Space>
                  <UserOutlined />
                  Human 입력창
                </Space>
              }
            >
              <Row gutter={[20, 20]}>
                <Col xs={24}>
                  <Form className="submitForm" layout="vertical" onFinish={submit}>
                    <Form.Item label="참가자 이름" required>
                      <Input
                        allowClear
                        disabled={isSubmitting}
                        onChange={(event) => setName(event.target.value)}
                        placeholder="예: 교수님 A"
                        prefix={<UserOutlined />}
                        size="large"
                        value={name}
                      />
                    </Form.Item>
                    <Form.Item
                      extra="추첨 연락용으로만 저장됩니다. 점수판에는 표시되지 않습니다."
                      label="전화번호 (선택)"
                    >
                      <Input
                        allowClear
                        disabled={isSubmitting}
                        inputMode="tel"
                        onChange={(event) => setPhoneNumber(event.target.value)}
                        placeholder="예: 010-1234-5678"
                        prefix={<PhoneOutlined />}
                        size="large"
                        value={phoneNumber}
                      />
                    </Form.Item>
                    <Form.Item label="시나리오 상황">
                      <Input.TextArea
                        autoSize={{ minRows: 3, maxRows: 5 }}
                        disabled={isSubmitting}
                        onChange={(event) => setScenarioSituation(event.target.value)}
                        placeholder="운영자가 케이스를 선택하면 시나리오가 표시됩니다."
                        value={scenarioSituation}
                      />
                    </Form.Item>
                    <Form.Item label="결과">
                      <Segmented
                        block
                        disabled={isSubmitting}
                        onChange={(value) => setStatus(value as "SUCCESS" | "FAIL")}
                        options={[
                          {
                            label: (
                              <Space size={6}>
                                <CheckCircleOutlined />
                                성공
                              </Space>
                            ),
                            value: "SUCCESS",
                          },
                          {
                            label: (
                              <Space size={6}>
                                <StopOutlined />
                                실패
                              </Space>
                            ),
                            value: "FAIL",
                          },
                        ]}
                        size="large"
                        value={status}
                      />
                    </Form.Item>
                    <Form.Item
                      help={
                        hasRequiredSuccessReason
                          ? `좋아요. 현재 ${successReasonLength}자 입력됨.`
                          : `성공 이유는 최소 ${MIN_SUCCESS_REASON_LENGTH}자 이상 작성해야 제출할 수 있습니다. 현재 ${successReasonLength}자.`
                      }
                      label={`성공 이유 (${MIN_SUCCESS_REASON_LENGTH}자 이상 필수)`}
                      required
                      validateStatus={successReasonLength > 0 && !hasRequiredSuccessReason ? "warning" : undefined}
                    >
                      <Input.TextArea
                        autoSize={{ minRows: 3, maxRows: 5 }}
                        disabled={isSubmitting}
                        onChange={(event) => setSuccessReason(event.target.value)}
                        placeholder="예: 최종 화면에서 주문 내역 진입 상태와 결제 완료 문구를 확인했습니다."
                        showCount
                        maxLength={180}
                        value={successReason}
                      />
                    </Form.Item>
                    <Form.Item label="증거 스크린샷" required>
                      <div
                        className={`${evidenceImages.length ? "pasteBox hasImage" : "pasteBox"}${isSubmitting ? " isSubmitting" : ""}`}
                        onPaste={handleScreenshotPaste}
                        tabIndex={0}
                      >
                        {evidenceImages.length ? (
                          <div className="pastePreviewMulti">
                            <div className="pastePreviewSummary">
                              <strong>스크린샷 {evidenceImages.length}개 첨부됨</strong>
                              <span>추가 캡처 후 다시 붙여넣으면 최대 {MAX_EVIDENCE_IMAGES}개까지 누적됩니다.</span>
                            </div>
                            <Image.PreviewGroup>
                              <div className="pastePreviewGrid">
                                {evidenceImages.map((image, index) => (
                                  <div className="pastePreviewItem" key={`${image.name}-${image.size}-${index}`}>
                                    <Image
                                      alt={`붙여넣은 증거 스크린샷 ${index + 1}`}
                                      preview={{ mask: "크게 보기" }}
                                      src={image.dataUrl}
                                    />
                                    <div>
                                      <span>
                                        {index + 1}. {image.width}x{image.height}
                                      </span>
                                      <Button
                                        disabled={isSubmitting}
                                        htmlType="button"
                                        icon={<DeleteOutlined />}
                                        onClick={() =>
                                          setEvidenceImages((currentImages) =>
                                            currentImages.filter((_, imageIndex) => imageIndex !== index),
                                          )
                                        }
                                        size="small"
                                      >
                                        제거
                                      </Button>
                                    </div>
                                  </div>
                                ))}
                              </div>
                            </Image.PreviewGroup>
                            {evidenceImages.length >= MAX_EVIDENCE_IMAGES ? (
                              <span className="pasteLimitText">최대 {MAX_EVIDENCE_IMAGES}개까지 첨부됨</span>
                            ) : null}
                          </div>
                        ) : (
                          <div className="pasteEmpty">
                            <PictureOutlined />
                            <strong>여기에 스크린샷 붙여넣기</strong>
                            <span>캡처 후 Cmd+V / Ctrl+V, 여러 번 붙여넣기 가능</span>
                          </div>
                        )}
                      </div>
                    </Form.Item>

                    <div className="submitRequirementBox">
                      <strong>제출 조건</strong>
                      <span className={hasRequiredSuccessReason ? "isMet" : ""}>
                        성공 이유 {MIN_SUCCESS_REASON_LENGTH}자 이상
                        <b>{successReasonLength} / {MIN_SUCCESS_REASON_LENGTH}</b>
                      </span>
                      <span className={hasRequiredScreenshot ? "isMet" : ""}>
                        증거 스크린샷 1개 이상
                        <b>{evidenceImages.length}개</b>
                      </span>
                    </div>

                    {locked ? <Alert icon={<LockOutlined />} message="제출 완료" showIcon type="success" /> : null}
                    {isSubmitting ? (
                      <Alert
                        description="서버에 기록을 저장하고 타이머를 종료하는 중입니다."
                        icon={<Spin size="small" />}
                        message="제출 중"
                        showIcon
                        type="info"
                      />
                    ) : null}

                    <Button block disabled={!submitReady} htmlType="submit" loading={isSubmitting} size="large" type="primary">
                      {locked ? "제출 완료" : isSubmitting ? "제출 중" : "완료 제출"}
                    </Button>
                    {message ? <Alert message={message.text} showIcon type={message.type} /> : null}
                  </Form>
                </Col>
              </Row>
            </Card>
          </Col>
          <Col xs={24} lg={9}>
            <Card
              className="gaiaInputCard audienceGaiaCard"
              extra={terminalGaiaStatus(currentGaiaRecord, realtimeStatus)}
              title={
                <Space>
                  <RobotOutlined />
                  GAIA 실시간 증거
                </Space>
              }
            >
              <GaiaLiveFeed records={gaiaRecords} storageMode={storageMode} />
            </Card>
          </Col>
        </Row>
      </section>
    </main>
  );
}

function GaiaLiveFeed({ records, storageMode }: { records: BattleRecord[]; storageMode: BattleStorageMode }) {
  if (!records.length) {
    return (
      <div className="gaiaWaitingState">
        <div className="gaiaSignal">
          <CloudUploadOutlined />
        </div>
        <strong>GAIA 증거 대기 중</strong>
        <p>GAIA 컴퓨터에서 테스트가 끝나면 최종 화면 증거가 여기에 바로 표시됩니다.</p>
        {storageMode === "memory" ? <span>실습용 배포 세션에서는 실제 기록만 표시됩니다.</span> : null}
      </div>
    );
  }

  return (
    <div className="gaiaEvidenceList">
      {records.map((record) => {
        const provider = metadataText(record.metadata?.provider);
        const model = metadataText(record.metadata?.model);
        const qaMode = metadataText(record.metadata?.qaMode);
        const linkUrl = evidenceLinkUrl(record);
        const imageUrls = evidenceImageUrls(record);
        return (
          <article className="gaiaEvidenceItem" key={`${record.id}-${record.updatedAt}`}>
            <div className="gaiaEvidenceHead">
              <strong>{record.scenarioLabel || record.scenarioId}</strong>
              <Tag color={record.status === "SUCCESS" ? "success" : record.status === "FAIL" ? "error" : "processing"}>
                {record.status}
              </Tag>
            </div>
            <p>{record.reason || "증거 수신됨"}</p>
            <div className="gaiaEvidenceMeta">
              <Tag>{seconds(record.durationSeconds)}</Tag>
              <Tag>{formatTime(record.updatedAt)}</Tag>
              {provider ? <Tag>{provider}</Tag> : null}
              {model ? <Tag>{model}</Tag> : null}
              {qaMode ? <Tag>{qaMode}</Tag> : null}
            </div>
            {linkUrl ? (
              <Button href={linkUrl} icon={<LinkOutlined />} size="small" target="_blank">
                증거 열기
              </Button>
            ) : null}
            {imageUrls.length ? (
              <Image.PreviewGroup>
                <div className="evidenceGallery gaiaEvidenceGallery">
                  {imageUrls.map((imageUrl, index) => (
                    <Image
                      alt={`${record.participantName} 증거 스크린샷 ${index + 1}`}
                      className="gaiaScreenshot"
                      key={`${imageUrl}-${index}`}
                      preview={{ mask: "크게 보기" }}
                      src={imageUrl}
                    />
                  ))}
                </div>
              </Image.PreviewGroup>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}
