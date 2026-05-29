"use client";

import {
  ClockCircleOutlined,
  DashboardOutlined,
  FileSearchOutlined,
  FormOutlined,
  LinkOutlined,
  ReloadOutlined,
  RobotOutlined,
  TrophyOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Badge, Button, Card, Col, Empty, Image, Progress, Row, Space, Statistic, Tag } from "antd";
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
  if (summary.bestHumanSeconds === null && summary.bestGaiaSeconds === null) return "대기 중";
  if (summary.bestHumanSeconds === null) return "GAIA 선공";
  if (summary.bestGaiaSeconds === null) return "Human 선공";
  if (summary.bestHumanSeconds < summary.bestGaiaSeconds) return "Human 리드";
  if (summary.bestGaiaSeconds < summary.bestHumanSeconds) return "GAIA 리드";
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

function evidenceImageUrl(record: BattleRecord) {
  const direct = metadataText(record.metadata?.evidenceImageDataUrl || record.metadata?.screenshotDataUrl);
  if (direct.startsWith("data:image/")) return direct;
  const url = metadataText(record.metadata?.screenshotUrl || record.artifactUrl);
  if (url.startsWith("data:image/")) return url;
  if (/\.(png|jpe?g|webp|gif)(\?|#|$)/i.test(url)) return url;
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
      return <Tag color="green">사람 우세</Tag>;
    case "GAIA_FASTER":
      return <Tag color="blue">GAIA 우세</Tag>;
    case "TIE":
      return <Tag color="gold">동률</Tag>;
    case "HUMAN_WIN":
      return <Tag color="green">사람만 성공</Tag>;
    case "GAIA_WIN":
      return <Tag color="blue">GAIA만 성공</Tag>;
    case "BOTH_FAILED":
      return <Tag color="error">둘 다 미성공</Tag>;
    default:
      return <Tag color="default">대기 중</Tag>;
  }
}

export function BattleBoardClient({ sessionId, initialRecords }: Props) {
  const [records, setRecords] = useState(initialRecords);
  const [updatedAt, setUpdatedAt] = useState("대기 중");
  const [mounted, setMounted] = useState(false);

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
          <Col xs={24} md={12} xl={6}>
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
          <Col xs={12} md={8} xl={5}>
            <Card className="metricCard">
              <Statistic prefix={<ClockCircleOutlined />} title="Best Human" value={seconds(summary.bestHumanSeconds)} />
            </Card>
          </Col>
          <Col xs={12} md={8} xl={5}>
            <Card className="metricCard">
              <Statistic title="Best GAIA" value={seconds(summary.bestGaiaSeconds)} />
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

        <Card
          className="casesCard"
          extra={<Tag color="processing">{cases.length} cases</Tag>}
          title={
            <Space>
              <FileSearchOutlined />
              케이스별 대결
            </Space>
          }
        >
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
        </Card>
      </section>
    </main>
  );
}

function CaseCard({ battleCase }: { battleCase: BattleCase }) {
  return (
    <Card className="caseCard">
      <div className="caseCardHead">
        <strong className="caseTitle">{battleCase.label}</strong>
        <Space size={8}>
          {verdictTag(battleCase.verdict)}
          <span className="evidenceTime">{formatTime(battleCase.latestAt)}</span>
        </Space>
      </div>
      <div className="caseSides">
        <CaseSide isHuman record={battleCase.bestHuman} attempts={battleCase.humans.length} />
        <div className="caseVs">VS</div>
        <CaseSide isHuman={false} record={battleCase.bestGaia} attempts={battleCase.gaias.length} />
      </div>
    </Card>
  );
}

function CaseSide({ isHuman, record, attempts }: { isHuman: boolean; record: BattleRecord | null; attempts: number }) {
  const imageUrl = record ? evidenceImageUrl(record) : "";
  const provider = record ? metadataText(record.metadata?.provider) : "";
  const model = record ? metadataText(record.metadata?.model) : "";
  const qaMode = record ? metadataText(record.metadata?.qaMode) : "";
  return (
    <div className={isHuman ? "caseSide humanSide" : "caseSide gaiaSide"}>
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
        {record ? statusTag(record.status) : <Tag color="default">대기</Tag>}
      </div>
      {record ? (
        <>
          <div className="caseSideTime">
            <ClockCircleOutlined />
            <strong className="monoValue">{seconds(record.durationSeconds)}</strong>
            <span>{formatTime(record.updatedAt)}</span>
          </div>
          <p>{record.reason || "증거 메모 없음"}</p>
          {imageUrl ? (
            <Image alt="증거 스크린샷" className="evidenceScreenshot" preview={false} src={imageUrl} />
          ) : null}
          {provider || model || qaMode || record.artifactUrl ? (
            <div className="evidenceMeta">
              {provider ? <Tag>{provider}</Tag> : null}
              {model ? <Tag>{model}</Tag> : null}
              {qaMode ? <Tag>{qaMode}</Tag> : null}
              {record.artifactUrl ? (
                <Button href={record.artifactUrl} icon={<LinkOutlined />} size="small" target="_blank">
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
