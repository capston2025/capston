"use client";

import { ApiOutlined, DashboardOutlined, FormOutlined, LinkOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Col, QRCode, Row, Space, Tag, Typography } from "antd";
import { useEffect, useState } from "react";

const fixedSessionId = process.env.NEXT_PUBLIC_DEFAULT_SESSION_ID || "battle-live";
const fallbackSiteUrl = "https://gaia-battle-web.vercel.app";

export default function NewBattlePage() {
  const [origin, setOrigin] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => setOrigin(window.location.origin), 0);
    return () => window.clearTimeout(timer);
  }, []);

  const sessionId = fixedSessionId;
  const siteUrl = origin || fallbackSiteUrl;
  const boardUrl = origin ? `${origin}/battle/${sessionId}` : `/battle/${sessionId}`;
  const humanUrl = origin ? `${origin}/battle/${sessionId}/human` : `/battle/${sessionId}/human`;
  const uploadUrl = origin ? `${origin}/api/records` : "/api/records";
  const gaiaEnv = [
    `GAIA_BATTLE_SITE_URL=${siteUrl}`,
    `GAIA_BATTLE_UPLOAD_URL=${uploadUrl}`,
    "GAIA_BATTLE_UPLOAD_TOKEN=",
    `GAIA_BATTLE_SESSION_ID=${sessionId}`,
    'GAIA_BATTLE_SCENARIO_LABEL="현장 QA 미션"',
  ].join("\n");
  const gaiaCommand =
    "python scripts/run_goal_benchmark.py --suite <suite.json> --limit 1 --battle-upload-url \"$GAIA_BATTLE_UPLOAD_URL\" --battle-session-id \"$GAIA_BATTLE_SESSION_ID\"";

  return (
    <main className="appFrame homeFrame">
      <section className="appContainer">
        <Card className="setupCard">
          <Row gutter={[28, 28]} align="middle">
            <Col xs={24} md={15}>
              <Space orientation="vertical" size={18}>
                <Tag color="processing" icon={<LinkOutlined />}>
                  오늘 세션
                </Tag>
                <div>
                  <h1>오늘 실습 세션이 준비됐습니다</h1>
                  <p className="heroCopy">세션은 하나만 씁니다. 사람 입력, 점수판, GAIA 업로드가 모두 같은 보드에 바로 기록됩니다.</p>
                </div>
                <div className="sessionCode">{sessionId}</div>
                <Space className="setupActions" wrap>
                  <Button href={boardUrl} icon={<DashboardOutlined />} size="large" type="primary">
                    점수판
                  </Button>
                  <Button href={humanUrl} icon={<FormOutlined />} size="large">
                    사람 입력
                  </Button>
                </Space>
                <Alert
                  message="GAIA 실행 컴퓨터 환경변수"
                  description={<Typography.Paragraph className="copyBlock" copyable={{ text: gaiaEnv }}>{gaiaEnv}</Typography.Paragraph>}
                  showIcon
                  type="info"
                />
                <Alert
                  message="실행 명령 예시"
                  description={
                    <Typography.Paragraph className="copyBlock" copyable={{ text: gaiaCommand }}>
                      {gaiaCommand}
                    </Typography.Paragraph>
                  }
                  icon={<ApiOutlined />}
                  showIcon
                  type="success"
                />
              </Space>
            </Col>
            <Col xs={24} md={9}>
              <div className="qrPanel">
                <QRCode value={origin ? humanUrl : `${fallbackSiteUrl}/battle/${sessionId}/human`} size={176} />
                <p>사람 입력 페이지를 바로 엽니다.</p>
                <code>{origin ? humanUrl : `${fallbackSiteUrl}/battle/${sessionId}/human`}</code>
                <code>{origin ? boardUrl : `${fallbackSiteUrl}/battle/${sessionId}`}</code>
                <code>{uploadUrl}</code>
              </div>
            </Col>
          </Row>
        </Card>
      </section>
    </main>
  );
}
