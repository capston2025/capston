"use client";

import { DashboardOutlined, FormOutlined, SettingOutlined, ThunderboltOutlined } from "@ant-design/icons";
import { Button, Card, Col, Row, Space, Tag } from "antd";

const defaultSessionId = process.env.NEXT_PUBLIC_DEFAULT_SESSION_ID || "battle-live";

export default function Home() {
  return (
    <main className="appFrame homeFrame">
      <section className="appContainer">
        <Card className="homeHero">
          <Row gutter={[32, 28]} align="middle">
            <Col xs={24} lg={15}>
              <Space orientation="vertical" size={18}>
                <Tag color="blue" icon={<ThunderboltOutlined />}>
                  Live QA Battle
                </Tag>
                <div>
                  <h1>사람과 GAIA의 QA 대결 기록판</h1>
                  <p className="heroCopy">
                    오늘은 고정 세션 하나에 사람 입력과 GAIA 기록을 모읍니다. 운영자는 점수판에서 시나리오를 적고
                    시작 신호만 보내면 됩니다.
                  </p>
                </div>
              </Space>
            </Col>
            <Col xs={24} lg={9}>
              <div className="homeActionPanel">
                <Button block href={`/battle/${defaultSessionId}/human`} icon={<FormOutlined />} size="large" type="primary">
                  사람 입력 열기
                </Button>
                <Button block href={`/battle/${defaultSessionId}`} icon={<DashboardOutlined />} size="large">
                  점수판 열기
                </Button>
                <Button block href="/battle/new" icon={<SettingOutlined />} size="large">
                  운영 설정
                </Button>
              </div>
            </Col>
          </Row>
        </Card>
      </section>
    </main>
  );
}
