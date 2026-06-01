"use client";

import {
  ClockCircleOutlined,
  DashboardOutlined,
  DeleteOutlined,
  FileSearchOutlined,
  FormOutlined,
  LinkOutlined,
  ReloadOutlined,
  RobotOutlined,
  TrophyOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Badge, Button, Card, Col, Empty, Image, Popconfirm, Progress, Row, Space, Statistic, Tag } from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import { BattleCase, CaseVerdict, groupBattleCases } from "@/lib/cases";
import { summarizeBattle } from "@/lib/summary";
import { BattleRecord } from "@/lib/types";
import { useBattleRealtime, type BattleRealtimeStatus } from "./useBattleRealtime";

type Props = {
  sessionId: string;
  initialRecords: BattleRecord[];
};

function seconds(value: number | null) {
  if (typeof value !== "number") return "-";
  return `${value.toFixed(value >= 10 ? 1 : 2)}s`;
}

function statusTag(status: BattleRecord["status"]) {
  if (status === "SUCCESS") return <Tag color="success">SUCCESS</Tag>;
  if (status === "FAIL") return <Tag color="error">FAIL</Tag>;
  if (status === "BLOCKED") return <Tag color="warning">BLOCKED</Tag>;
  return <Tag color="processing">RUNNING</Tag>;
}

function winner(summary: ReturnType<typeof summarizeBattle>) {
  if (summary.averageHumanSeconds === null && summary.averageGaiaSeconds === null) return "대기 중";
  if (summary.averageHumanSeconds === null) return "GAIA 선공";
  if (summary.averageGaiaSeconds === null) return "Human 선공";
  if (summary.averageHumanSeconds < summary.averageGaiaSeconds) return "Human 리드";
  if (summary.averageGaiaSeconds < summary.averageHumanSeconds) return "GAIA 리드";
  return "동률";
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

function realtimeTag(status: BattleRealtimeStatus) {
  if (status === "subscribed") return <Tag color="processing">realtime</Tag>;
  if (status === "connecting") return <Tag color="blue">connecting</Tag>;
  return <Tag color="default">polling</Tag>;
}

function verdictTag(verdict: CaseVerdict) {
  switch (verdict) {
    case "HUMAN_FASTER":
      return <Tag color="green">Human 승리</Tag>;
    case "GAIA_FASTER":
      return <Tag color="blue">GAIA 승리</Tag>;
    case "TIE":
      return <Tag color="gold">동률</Tag>;
    case "HUMAN_WIN":
      return <Tag color="green">Human 승리</Tag>;
    case "GAIA_WIN":
      return <Tag color="blue">GAIA 승리</Tag>;
    case "BOTH_FAILED":
      return <Tag color="error">둘 다 미성공</Tag>;
    default:
      return <Tag color="default">대기 중</Tag>;
  }
}

function sideOutcome(verdict: CaseVerdict, isHuman: boolean) {
  if (verdict === "TIE") return "tie";
  if (verdict === "BOTH_FAILED") return "failed";
  if (verdict === "WAITING") return "waiting";
  const humanWon = verdict === "HUMAN_FASTER" || verdict === "HUMAN_WIN";
  const gaiaWon = verdict === "GAIA_FASTER" || verdict === "GAIA_WIN";
  if ((isHuman && humanWon) || (!isHuman && gaiaWon)) return "winner";
  return "loser";
}

function outcomeTag(outcome: string) {
  if (outcome === "winner") return <Tag color="success">승리</Tag>;
  if (outcome === "loser") return <Tag color="default">패배</Tag>;
  if (outcome === "tie") return <Tag color="gold">동률</Tag>;
  if (outcome === "failed") return <Tag color="error">미성공</Tag>;
  return null;
}

function verdictDetail(battleCase: BattleCase) {
  const humanSeconds = battleCase.bestHuman?.durationSeconds;
  const gaiaSeconds = battleCase.bestGaia?.durationSeconds;
  if (battleCase.verdict === "HUMAN_FASTER" && typeof humanSeconds === "number" && typeof gaiaSeconds === "number") {
    return `Human이 ${(gaiaSeconds - humanSeconds).toFixed(1)}s 빠름`;
  }
  if (battleCase.verdict === "GAIA_FASTER" && typeof humanSeconds === "number" && typeof gaiaSeconds === "number") {
    return `GAIA가 ${(humanSeconds - gaiaSeconds).toFixed(1)}s 빠름`;
  }
  if (battleCase.verdict === "HUMAN_WIN") return "Human만 성공";
  if (battleCase.verdict === "GAIA_WIN") return "GAIA만 성공";
  if (battleCase.verdict === "BOTH_FAILED") return "성공 기록 없음";
  if (battleCase.verdict === "TIE") return "같은 시간";
  return "대기 중";
}

export function BattleBoardClient({ sessionId, initialRecords }: Props) {
  const [records, setRecords] = useState(initialRecords);
  const [updatedAt, setUpdatedAt] = useState("대기 중");
  const [mounted, setMounted] = useState(false);
  const [resetting, setResetting] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setMounted(true), 0);
    return () => window.clearTimeout(timer);
  }, []);

  const refreshRecords = useCallback(async () => {
    const response = await fetch(`/api/records?sessionId=${encodeURIComponent(sessionId)}`, { cache: "no-store" });
    if (!response.ok) return;
    const data = (await response.json()) as { records: BattleRecord[] };
    setRecords(data.records || []);
    setUpdatedAt(new Date().toLocaleTimeString("ko-KR"));
  }, [sessionId]);

  const refreshSession = useCallback(async () => {
    const response = await fetch(`/api/session?sessionId=${encodeURIComponent(sessionId)}`, { cache: "no-store" });
    if (!response.ok) return;
    setUpdatedAt(new Date().toLocaleTimeString("ko-KR"));
  }, [sessionId]);

  const resetSession = useCallback(async () => {
    setResetting(true);
    try {
      const response = await fetch(`/api/session?sessionId=${encodeURIComponent(sessionId)}`, {
        method: "DELETE",
      });
      if (!response.ok) {
        const data = (await response.json().catch(() => ({}))) as { error?: string };
        window.alert(data.error || "세션 초기화 실패");
        return;
      }
      setRecords([]);
      setUpdatedAt(`${new Date().toLocaleTimeString("ko-KR")} 초기화`);
      void refreshSession();
    } finally {
      setResetting(false);
    }
  }, [refreshSession, sessionId]);

  const realtimeStatus = useBattleRealtime({
    sessionId,
    onRecordsChange: refreshRecords,
    onSessionChange: refreshSession,
  });
  const shouldPoll = realtimeStatus !== "subscribed";

  useEffect(() => {
    const initialTimer = window.setTimeout(() => {
      void refreshRecords();
      void refreshSession();
    }, 0);
    if (!shouldPoll) return () => window.clearTimeout(initialTimer);
    const timer = window.setInterval(() => {
      void refreshRecords();
      void refreshSession();
    }, 1500);
    return () => {
      window.clearTimeout(initialTimer);
      window.clearInterval(timer);
    };
  }, [refreshRecords, refreshSession, shouldPoll]);

  const summary = useMemo(() => summarizeBattle(records), [records]);
  const cases = useMemo(() => groupBattleCases(records), [records]);
  const successRate = summary.total ? Math.round((summary.successTotal / summary.total) * 100) : 0;

  if (!mounted) {
    return (
      <main className="appFrame">
        <section className="appContainer boardContainer">
          <Card className="boardHero">
            <Badge status="processing" text="Live QA Battle" />
            <h1>Human vs GAIA</h1>
          </Card>
        </section>
      </main>
    );
  }

  return (
    <main className="appFrame">
      <section className="appContainer boardContainer">
        <Card className="boardHero">
          <div className="boardHeroTop">
            <Space orientation="vertical" size={10}>
              <Badge status="processing" text="Live QA Battle" />
              <div>
                <h1>Human vs GAIA</h1>
              </div>
            </Space>
            <Space wrap>
              <Button href={`/battle/${sessionId}/human`} icon={<FormOutlined />} size="large" type="primary">
                사람 입력
              </Button>
              <Popconfirm
                cancelText="취소"
                description="현재 세션의 시작 신호와 기록을 모두 지웁니다."
                okButtonProps={{ danger: true, loading: resetting }}
                okText="초기화"
                onConfirm={resetSession}
                title="세션을 초기화할까요?"
              >
                <Button danger icon={<DeleteOutlined />} loading={resetting} size="large">
                  초기화
                </Button>
              </Popconfirm>
              <Button href="/" icon={<DashboardOutlined />} size="large">
                홈
              </Button>
            </Space>
          </div>
          <div className="sessionStrip">
            <span>Session</span>
            <code>{sessionId}</code>
            {realtimeTag(realtimeStatus)}
            <span className="refreshStamp">
              <ReloadOutlined />
              {updatedAt} 자동 갱신
            </span>
          </div>
        </Card>

        <Row gutter={[14, 14]} className="metricRow">
          <Col xs={24} md={12} xl={8}>
            <Card className="metricCard leadCard">
              <Statistic prefix={<TrophyOutlined />} title="현재 리드" value={winner(summary)} />
            </Card>
          </Col>
          <Col xs={12} md={6} xl={4}>
            <Card className="metricCard">
              <Statistic prefix={<UserOutlined />} title="Human 제출" value={summary.humanTotal} />
            </Card>
          </Col>
          <Col xs={12} md={6} xl={4}>
            <Card className="metricCard">
              <Statistic prefix={<RobotOutlined />} title="GAIA 기록" value={summary.gaiaTotal} />
            </Card>
          </Col>
          <Col xs={12} md={8} xl={4}>
            <Card className="metricCard">
              <Statistic prefix={<ClockCircleOutlined />} title="평균 Human" value={seconds(summary.averageHumanSeconds)} />
            </Card>
          </Col>
          <Col xs={12} md={8} xl={4}>
            <Card className="metricCard">
              <Statistic title="평균 GAIA" value={seconds(summary.averageGaiaSeconds)} />
            </Card>
          </Col>
          <Col xs={24} md={8} xl={24}>
            <Card className="metricCard progressCard">
              <div>
                <span>성공률</span>
                <strong>{successRate}%</strong>
              </div>
              <Progress percent={successRate} showInfo={false} status={successRate >= 50 ? "active" : "exception"} />
            </Card>
          </Col>
        </Row>

        <section className="casesSection">
          <div className="casesSectionHead">
            <h2 className="sectionTitle">
              <FileSearchOutlined />
              케이스별 대결
            </h2>
            <Tag color="processing">{cases.length} cases</Tag>
          </div>
          {cases.length ? (
            <div className="caseList">
              {cases.map((battleCase) => (
                <CaseCard battleCase={battleCase} key={battleCase.scenarioId} />
              ))}
            </div>
          ) : (
            <Empty
              description="GAIA가 케이스를 시작하면 같은 상황의 사람·GAIA 결과가 케이스별로 묶여 표시됩니다."
              image={Empty.PRESENTED_IMAGE_SIMPLE}
            />
          )}
        </section>
      </section>
    </main>
  );
}

function CaseCard({ battleCase }: { battleCase: BattleCase }) {
  return (
    <Card className={`caseCard caseCard-${battleCase.verdict.toLowerCase()}`}>
      <div className="caseCardHead">
        <strong className="caseTitle">{battleCase.label}</strong>
        <Space className="caseVerdictSummary" size={8}>
          {verdictTag(battleCase.verdict)}
          <span>{verdictDetail(battleCase)}</span>
          <span className="evidenceTime">{formatTime(battleCase.latestAt)}</span>
        </Space>
      </div>
      <div className="caseSides">
        <CaseSide
          attempts={battleCase.humans.length}
          isHuman
          outcome={sideOutcome(battleCase.verdict, true)}
          record={battleCase.bestHuman}
        />
        <div className="caseVs">VS</div>
        <CaseSide
          attempts={battleCase.gaias.length}
          isHuman={false}
          outcome={sideOutcome(battleCase.verdict, false)}
          record={battleCase.bestGaia}
        />
      </div>
    </Card>
  );
}

function CaseSide({
  isHuman,
  record,
  attempts,
  outcome,
}: {
  isHuman: boolean;
  record: BattleRecord | null;
  attempts: number;
  outcome: string;
}) {
  const imageUrls = record ? evidenceImageUrls(record) : [];
  const linkUrl = record ? evidenceLinkUrl(record) : "";
  const provider = record ? metadataText(record.metadata?.provider) : "";
  const model = record ? metadataText(record.metadata?.model) : "";
  const qaMode = record ? metadataText(record.metadata?.qaMode) : "";
  return (
    <div className={`${isHuman ? "caseSide humanSide" : "caseSide gaiaSide"} outcome-${outcome}`}>
      <div className="caseSideHead">
        <Space size={10}>
          <span className={isHuman ? "avatarIcon humanAvatar" : "avatarIcon gaiaAvatar"}>
            {isHuman ? <UserOutlined /> : <RobotOutlined />}
          </span>
          <span className="recordIdentity">
            <strong>{record ? record.participantName : isHuman ? "사람" : "GAIA"}</strong>
            <small>
              {isHuman ? "Human" : "GAIA"}
              {attempts > 1 ? ` · ${attempts}회 기록` : ""}
            </small>
          </span>
        </Space>
        <Space size={6}>
          {outcomeTag(outcome)}
          {record ? statusTag(record.status) : <Tag color="default">대기</Tag>}
        </Space>
      </div>
      {record ? (
        <>
          <div className="caseSideTime">
            <ClockCircleOutlined />
            <strong className="monoValue">{seconds(record.durationSeconds)}</strong>
            <span>{formatTime(record.updatedAt)}</span>
          </div>
          <p>{record.reason || "성공 이유 없음"}</p>
          {imageUrls.length ? (
            <Image.PreviewGroup>
              <div className="evidenceGallery">
                {imageUrls.map((imageUrl, index) => (
                  <Image
                    alt={`증거 스크린샷 ${index + 1}`}
                    className="evidenceScreenshot"
                    key={`${imageUrl}-${index}`}
                    preview={{ mask: "크게 보기" }}
                    src={imageUrl}
                  />
                ))}
              </div>
            </Image.PreviewGroup>
          ) : null}
          {provider || model || qaMode || linkUrl ? (
            <div className="evidenceMeta">
              {provider ? <Tag>{provider}</Tag> : null}
              {model ? <Tag>{model}</Tag> : null}
              {qaMode ? <Tag>{qaMode}</Tag> : null}
              {linkUrl ? (
                <Button href={linkUrl} icon={<LinkOutlined />} size="small" target="_blank">
                  증거 열기
                </Button>
              ) : null}
            </div>
          ) : null}
        </>
      ) : (
        <div className="caseSideEmpty">{isHuman ? "사람 기록 대기" : "GAIA 기록 대기"}</div>
      )}
    </div>
  );
}
