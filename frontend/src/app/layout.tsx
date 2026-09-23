import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "pokeHelper",
  description: "포켓몬 챔피언스 파티 구축 + 선출 판단 보조",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="ko">
      <body className="min-h-screen bg-slate-950 text-slate-100 antialiased">{children}</body>
    </html>
  );
}
