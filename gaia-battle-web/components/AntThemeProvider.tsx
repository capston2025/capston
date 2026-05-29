"use client";

import { ConfigProvider } from "antd";
import koKR from "antd/locale/ko_KR";
import type { ReactNode } from "react";

export function AntThemeProvider({ children }: { children: ReactNode }) {
  return (
    <ConfigProvider
      locale={koKR}
      theme={{
        token: {
          colorPrimary: "#1677ff",
          borderRadius: 8,
          colorBgLayout: "#f4f7fb",
          colorText: "#172033",
          colorTextSecondary: "#667085",
          colorBorderSecondary: "#e6ebf2",
          fontFamily:
            "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, \"Segoe UI\", sans-serif",
        },
        components: {
          Button: {
            controlHeight: 42,
            fontWeight: 700,
          },
          Card: {
            borderRadiusLG: 10,
          },
          Statistic: {
            titleFontSize: 13,
            contentFontSize: 30,
          },
          Table: {
            headerBg: "#f8fafc",
            headerColor: "#475467",
            rowHoverBg: "#f5f9ff",
          },
        },
      }}
    >
      {children}
    </ConfigProvider>
  );
}
