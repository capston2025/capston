"use client";

import {
  ClockCircleOutlined,
  DashboardOutlined,
  FileSearchOutlined,
  FormOutlined,
  LinkOutlined,
  PaperClipOutlined,
  PictureOutlined,
  ReloadOutlined,
  RobotOutlined,
  TrophyOutlined,
  UserOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Empty,
  Image,
  Progress,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { useCallback, useEffect, useMemo, useState } from "react";

import { summarizeBattle } from "@/lib/summary";
import { BattleRecord, BattleStorageMode } from "@/lib/types";
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

function recordColumns(): ColumnsType<BattleRecord> {
  return [
    {
      title: "참가자",
      dataIndex: "participantName",
      key: "participantName",
      render: (name: string, record) => (
        <Space size={10}>
          <span className={record.participantType === "human" ? "avatarIcon humanAvatar" : "avatarIcon gaiaAvatar"}>
            {record.participantType === "human" ? <UserOutlined /> : <RobotOutlined />}
          </span>
          <span className="recordIdentity">
            <strong>{name}</strong>
            <small>{record.scenarioLabel || record.scenarioId}</small>
          </span>
        </Space>
      ),
    },
    {
      title: "상태",
      dataIndex: "status",
      key: "status",
      align: "center",
      width: 116,
      render: (status: BattleRecord["status"]) => statusTag(status),
    },
    {
      title: "소요",
      dataIndex: "durationSeconds",
      key: "durationSeconds",
      align: "right",
      width: 96,
      render: (value: number | null) => <strong className="monoValue">{seconds(value)}</strong>,
    },
    {
      title: "증거",
      key: "evidence",
      ellipsis: true,
      render: (_, record) => <EvidenceInline record={record} />,
    },
  ];
}

export function BattleBoardClient({ sessionId, initialRecords }: Props) {
  const [records, setRecords] = useState(initialRecords);
  const [updatedAt, setUpdatedAt] = useState("대기 중");
  const [storageMode, setStorageMode] = useState<BattleStorageMode>("memory");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setMounted(true), 0);
    return () => window.clearTimeout(timer);
  }, []);

  const refreshRecords = useCallback(async () => {
    const response = await fetch(`/api/records?sessionId=${encodeURIComponent(sessionId)}`, { cache: "no-store" });
    if (!response.ok) return;
    const data = (await response.json()) as { records: BattleRecord[]; storage?: BattleStorageMode };
    setRecords(data.records || []);
    if (data.storage) setStorageMode(data.storage);
    setUpdatedAt(new Date().toLocaleTimeString("ko-KR"));
  }, [sessionId]);

  const refreshSession = useCallback(async () => {
    const response = await fetch(`/api/session?sessionId=${encodeURIComponent(sessionId)}`, { cache: "no-store" });
    if (!response.ok) return;
    const data = (await response.json()) as { storage?: BattleStorageMode };
    if (data.storage) setStorageMode(data.storage);
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
  const humanRecords = records.filter((record) => record.participantType === "human");
  const gaiaRecords = records.filter((record) => record.participantType === "gaia");
  const evidenceRecords = [...records]
    .filter((record) => record.reason || record.artifactUrl || Object.keys(record.metadata || {}).length)
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  const successRate = summary.total ? Math.round((summary.successTotal / summary.total) * 100) : 0;
  const columns = useMemo(() => recordColumns(), []);

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
                <p className="heroCopy">GAIA 벤치 실행 신호가 들어오면 참가자 화면 타이머가 자동으로 흐르고, 결과와 증거가 실시간으로 합류합니다.</p>
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
            <Tag color={storageMode === "supabase" ? "success" : "warning"}>
              storage: {storageMode}
            </Tag>
            {realtimeTag(realtimeStatus)}
            <span className="refreshStamp">
              <ReloadOutlined />
              {updatedAt} 자동 갱신
            </span>
          </div>
        </Card>

        {storageMode === "memory" ? (
          <Alert
            className="storageAlert"
            message="현재 기록은 서버 메모리에만 저장됩니다. Vercel 배포 시에는 Supabase 환경변수를 연결해야 여러 컴퓨터에서 안정적으로 공유됩니다."
            showIcon
            type="warning"
          />
        ) : null}

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
              <Statistic
                prefix={<ClockCircleOutlined />}
                title="Best Human"
                value={seconds(summary.bestHumanSeconds)}
              />
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

        <Row gutter={[16, 16]} className="tableRow">
          <Col xs={24} lg={12}>
            <Card
              className="recordsCard"
              extra={<Tag color="blue">{humanRecords.length}</Tag>}
              title={
                <Space>
                  <UserOutlined />
                  Human Runs
                </Space>
              }
            >
              <RecordTable columns={columns} empty="아직 사람 QA 기록이 없습니다." records={humanRecords} />
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card
              className="recordsCard"
              extra={<Tag color="geekblue">{gaiaRecords.length}</Tag>}
              title={
                <Space>
                  <RobotOutlined />
                  GAIA Runs
                </Space>
              }
            >
              <RecordTable columns={columns} empty="아직 GAIA 업로드 기록이 없습니다." records={gaiaRecords} />
            </Card>
          </Col>
        </Row>

        <Card
          className="evidenceCard"
          extra={<Tag color="processing">{evidenceRecords.length} items</Tag>}
          title={
            <Space>
              <FileSearchOutlined />
              Live Evidence Feed
            </Space>
          }
        >
          <EvidenceFeed records={evidenceRecords} />
        </Card>
      </section>
    </main>
  );
}

function RecordTable({
  records,
  empty,
  columns,
}: {
  records: BattleRecord[];
  empty: string;
  columns: ColumnsType<BattleRecord>;
}) {
  if (!records.length) {
    return <Empty description={empty} image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }

  return (
    <Table
      columns={columns}
      dataSource={records}
      pagination={false}
      rowKey={(record) => record.id}
      scroll={{ x: 520 }}
      size="middle"
    />
  );
}

function EvidenceInline({ record }: { record: BattleRecord }) {
  const note = record.reason || "증거 메모 없음";
  const imageUrl = evidenceImageUrl(record);
  return (
    <Space size={8} wrap>
      {imageUrl ? (
        <Tag color="blue" icon={<PictureOutlined />}>
          스크린샷
        </Tag>
      ) : null}
      <Typography.Text className="evidenceInline" title={note}>
        {note}
      </Typography.Text>
      {record.artifactUrl ? (
        <Button href={record.artifactUrl} icon={<PaperClipOutlined />} size="small" target="_blank" type="link">
          링크
        </Button>
      ) : null}
    </Space>
  );
}

function EvidenceFeed({ records }: { records: BattleRecord[] }) {
  if (!records.length) {
    return <Empty description="Human 또는 GAIA 기록이 올라오면 증거가 실시간으로 표시됩니다." image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  }

  return (
    <div className="evidenceGrid">
      {records.map((record) => {
        const provider = metadataText(record.metadata?.provider);
        const model = metadataText(record.metadata?.model);
        const qaMode = metadataText(record.metadata?.qaMode);
        const imageUrl = evidenceImageUrl(record);
        return (
          <article className="evidenceItem" key={`${record.id}-${record.updatedAt}`}>
            <div className="evidenceItemHeader">
              <Space size={10}>
                <span className={record.participantType === "human" ? "avatarIcon humanAvatar" : "avatarIcon gaiaAvatar"}>
                  {record.participantType === "human" ? <UserOutlined /> : <RobotOutlined />}
                </span>
                <span className="recordIdentity">
                  <strong>{record.participantName}</strong>
                  <small>{record.scenarioLabel || record.scenarioId}</small>
                </span>
              </Space>
              <Space size={8}>
                {statusTag(record.status)}
                <span className="evidenceTime">{formatTime(record.updatedAt)}</span>
              </Space>
            </div>
            <p>{record.reason || "증거 메모 없음"}</p>
            {imageUrl ? (
              <Image
                alt={`${record.participantName} 증거 스크린샷`}
                className="evidenceScreenshot"
                preview={false}
                src={imageUrl}
              />
            ) : null}
            <div className="evidenceMeta">
              <Tag color={record.participantType === "human" ? "green" : "blue"}>
                {record.participantType === "human" ? "Human Evidence" : "GAIA Evidence"}
              </Tag>
              {provider ? <Tag>{provider}</Tag> : null}
              {model ? <Tag>{model}</Tag> : null}
              {qaMode ? <Tag>{qaMode}</Tag> : null}
              {record.artifactUrl ? (
                <Button href={record.artifactUrl} icon={<LinkOutlined />} size="small" target="_blank">
                  증거 열기
                </Button>
              ) : null}
            </div>
          </article>
        );
      })}
    </div>
  );
}
