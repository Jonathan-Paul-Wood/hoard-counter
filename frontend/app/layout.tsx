import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Hoard Counter",
  description:
    "A little clarity for everything you hold. Private household finances, budgets, and planning.",
};
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
