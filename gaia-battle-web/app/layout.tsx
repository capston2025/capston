import type { Metadata } from "next";
import { AntdRegistry } from "@ant-design/nextjs-registry";

import { AntThemeProvider } from "@/components/AntThemeProvider";
import "./globals.css";

export const metadata: Metadata = {
  title: "Human vs GAIA Battle Board",
  description: "Live QA battle board for the final demo",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body>
        <AntdRegistry>
          <AntThemeProvider>{children}</AntThemeProvider>
        </AntdRegistry>
      </body>
    </html>
  );
}
