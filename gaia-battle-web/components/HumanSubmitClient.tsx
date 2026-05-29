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

function evidenceImageUrl(record: BattleRecord) {
  const direct = metadataText(record.metadata?.evidenceImageDataUrl || record.metadata?.screenshotDataUrl);
  if (direct.startsWith("data:image/")) return direct;
  const url = metadataText(record.metadata?.screenshotUrl || record.artifactUrl);
  if (url.startsWith("data:image/")) return url;
  if (/\.(png|jpe?g|webp|gif)(\?|#|$)/i.test(url)) return url;
  return "";
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
  const maxWidth = 1200;
  const scale = Math.min(1, maxWidth / Math.max(1, image.naturalWidth));
  const width = Math.max(1, Math.round(image.naturalWidth * scale));
  const height = Math.max(1, Math.round(image.naturalHeight * scale));
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("canvas unavailable");
  context.drawImage(image, 0, 0, width, height);
  const dataUrl = canvas.toDataURL("image/jpeg", 0.82);
  return { dataUrl, name: file.name || "pasted-screenshot.jpg", size: dataUrl.length, width, height };
}

export function HumanSubmitClient({ sessionId, scenarioId, initialRecords }: Props) {
  const [name, setName] = useState("");
  const [status, setStatus] = useState<"SUCCESS" | "FAIL">("SUCCESS");
  const [sessionStartedAt, setSessionStartedAt] = useState<string | null>(null);
  // The operator-visible "상황". The matching key (scenarioId) is received from
  // the session state and never shown to the participant.
  const [sessionScenarioId, setSessionScenarioId] = useState<string | null>(null);
  const [scenarioSituation, setScenarioSituation] = useState("");
  const [evidenceNote, setEvidenceNote] = useState("");
  const [evidenceImage, setEvidenceImage] = useState<EvidenceImage | null>(null);
  const [records, setRecords] = useState(initialRecords);
  const [message, setMessage] = useState<{ type: "success" | "warning" | "error"; text: string } | null>(null);
  const [now, setNow] = useState(0);
  const [serverClockOffsetMs, setServerClockOffsetMs] = useState(0);
  const [storageMode, setStorageMode] = useState<BattleStorageMode>("memory");
  const [mounted, setMounted] = useState(false);
  const lastScenarioRef = useRef<string | null>(null);

  const effectiveScenarioId = sessionScenarioId || scenarioId;
  const submittedRecord = useMemo(() => {
    const id = participantId(name);
    if (!name.trim()) return null;
    return (
      records.find(
        (record) =>
          record.participantType === "human" &&
          record.participantId === id &&
          record.scenarioId === effectiveScenarioId,
      ) || null
    );
  }, [name, records, effectiveScenarioId]);
  const locked = Boolean(submittedRecord);

  useEffect(() => {
    if (!effectiveScenarioId) return;
    if (lastScenarioRef.current === null) {
      lastScenarioRef.current = effectiveScenarioId;
      return;
    }
    if (lastScenarioRef.current === effectiveScenarioId) return;
    lastScenarioRef.current = effectiveScenarioId;
    setName("");
    setStatus("SUCCESS");
    setEvidenceNote("");
    setEvidenceImage(null);
    setMessage(null);
  }, [effectiveScenarioId]);

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
    }
    if (data.storage) setStorageMode(data.storage);
    setSessionStartedAt(data.state?.humanStartedAt || null);
    setSessionScenarioId(data.state?.scenarioId || null);
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
    if (!shouldPoll) return () => window.clearTimeout(initialTimer);
    const timer = window.setInterval(refreshSession, 500);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [refreshSession, shouldPoll]);

  useEffect(() => {
    const initialTimer = window.setTimeout(() => void refreshRecords(), 0);
    if (!shouldPoll) return () => window.clearTimeout(initialTimer);
    const timer = window.setInterval(refreshRecords, 1500);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [refreshRecords, shouldPoll]);

  const sessionStartTime = useMemo(() => {
    if (!sessionStartedAt) return null;
    const parsed = new Date(sessionStartedAt).getTime();
    return Number.isNaN(parsed) ? null : parsed;
  }, [sessionStartedAt]);

  useEffect(() => {
    if (!sessionStartTime || locked) return;
    const timer = window.setInterval(() => setNow(Date.now() + serverClockOffsetMs), 100);
    return () => window.clearInterval(timer);
  }, [locked, sessionStartTime, serverClockOffsetMs]);

  const liveDuration = sessionStartTime && now ? Math.max(0, Math.round(((now - sessionStartTime) / 1000) * 100) / 100) : null;
  const submittedDuration =
    typeof submittedRecord?.durationSeconds === "number" ? submittedRecord.durationSeconds : null;
  const displayDuration = submittedDuration ?? liveDuration;
  const timerLabel = submittedRecord ? "제출 완료" : sessionStartTime ? "측정 중" : "시작 대기";
  const timerHelp = submittedRecord ? "타이머 종료" : sessionStartTime ? "타이머 자동 실행 중" : "점수판 시작 대기";
  const gaiaRecords = useMemo(
    () => {
      if (storageMode === "memory") return [];
      return records
        .filter(
          (record) =>
            record.participantType === "gaia" &&
            record.scenarioId === effectiveScenarioId &&
            !isDemoLikeRecord(record),
        )
        .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
    },
    [effectiveScenarioId, records, storageMode],
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
    const item = Array.from(event.clipboardData.items).find((entry) => entry.type.startsWith("image/"));
    if (!item) {
      setMessage({ type: "warning", text: "클립보드에 이미지가 없습니다." });
      return;
    }
    event.preventDefault();
    const file = item.getAsFile();
    if (!file) {
      setMessage({ type: "error", text: "스크린샷을 읽지 못했습니다." });
      return;
    }
    try {
      const compressed = await compressScreenshot(file);
      setEvidenceImage(compressed);
      setMessage({ type: "success", text: "스크린샷이 붙었습니다." });
    } catch {
      setMessage({ type: "error", text: "스크린샷 처리 실패" });
    }
  }

  async function submit() {
    const cleanName = name.trim();
    if (!cleanName) {
      setMessage({ type: "warning", text: "이름을 먼저 입력해줘." });
      return;
    }
    if (!sessionStartTime) {
      setMessage({ type: "warning", text: "아직 시작 신호가 없습니다." });
      return;
    }
    if (locked) {
      setMessage({ type: "warning", text: "이미 기록됨" });
      return;
    }
    const finalDuration = Math.max(0, Math.round((((now || Date.now()) - sessionStartTime) / 1000) * 100) / 100);
    const cleanSituation = scenarioSituation.trim();
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
        reason: evidenceNote.trim() || (status === "SUCCESS" ? "사람 QA 완료" : "사람 QA 실패"),
        artifactUrl: "",
        metadata: {
          evidenceSource: "human-form",
          sessionStartedAt,
          scenarioSituation: cleanSituation,
          evidenceImageDataUrl: evidenceImage?.dataUrl || "",
          evidenceImageName: evidenceImage?.name || "",
          evidenceImageSize: evidenceImage?.size || 0,
          evidenceImageWidth: evidenceImage?.width || 0,
          evidenceImageHeight: evidenceImage?.height || 0,
        },
      }),
    });
    const data = (await response.json()) as { record?: BattleRecord; records?: BattleRecord[]; error?: string };
    if (!response.ok) {
      setMessage({ type: "error", text: data.error || "제출 실패" });
      return;
    }
    setRecords(data.records || (data.record ? [data.record, ...records] : []));
    setMessage({ type: "success", text: "제출 완료. 타이머 종료." });
  }

  return (
    <main className="appFrame humanFrame">
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
                        onChange={(event) => setName(event.target.value)}
                        placeholder="예: 교수님 A"
                        prefix={<UserOutlined />}
                        size="large"
                        value={name}
                      />
                    </Form.Item>
                    <Form.Item label="시나리오 상황">
                      <Input.TextArea
                        autoSize={{ minRows: 3, maxRows: 5 }}
                        onChange={(event) => setScenarioSituation(event.target.value)}
                        placeholder="운영자가 시작하면 시나리오가 표시됩니다."
                        value={scenarioSituation}
                      />
                    </Form.Item>
                    <Form.Item label="결과">
                      <Segmented
                        block
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
                    <Form.Item label="증거 메모">
                      <Input.TextArea
                        autoSize={{ minRows: 3, maxRows: 5 }}
                        onChange={(event) => setEvidenceNote(event.target.value)}
                        placeholder="예: 로그인 성공 후 주문 내역 화면까지 확인"
                        showCount
                        maxLength={180}
                        value={evidenceNote}
                      />
                    </Form.Item>
                    <Form.Item label="증거 스크린샷">
                      <div
                        className={evidenceImage ? "pasteBox hasImage" : "pasteBox"}
                        onPaste={handleScreenshotPaste}
                        tabIndex={0}
                      >
                        {evidenceImage ? (
                          <div className="pastePreview">
                            <Image alt="붙여넣은 증거 스크린샷" preview={false} src={evidenceImage.dataUrl} />
                            <div>
                              <strong>스크린샷 첨부됨</strong>
                              <span>
                                {evidenceImage.width}x{evidenceImage.height}
                              </span>
                              <Button htmlType="button" icon={<DeleteOutlined />} onClick={() => setEvidenceImage(null)} size="small">
                                제거
                              </Button>
                            </div>
                          </div>
                        ) : (
                          <div className="pasteEmpty">
                            <PictureOutlined />
                            <strong>여기에 스크린샷 붙여넣기</strong>
                            <span>캡처 후 Cmd+V / Ctrl+V</span>
                          </div>
                        )}
                      </div>
                    </Form.Item>

                    {locked ? <Alert icon={<LockOutlined />} message="제출 완료" showIcon type="success" /> : null}

                    <Button block disabled={locked} htmlType="submit" size="large" type="primary">
                      {locked ? "제출 완료" : "완료 제출"}
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
            {record.artifactUrl ? (
              <Button href={record.artifactUrl} icon={<LinkOutlined />} size="small" target="_blank">
                증거 열기
              </Button>
            ) : null}
            {evidenceImageUrl(record) ? (
              <Image
                alt={`${record.participantName} 증거 스크린샷`}
                className="gaiaScreenshot"
                preview={false}
                src={evidenceImageUrl(record)}
              />
            ) : null}
          </article>
        );
      })}
    </div>
  );
}
