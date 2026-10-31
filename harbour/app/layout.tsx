import type { Metadata } from "next";
import "./globals.css";
import "./simple.css";

export const metadata: Metadata = {
  title: "Harbour · Greater Vancouver, understood",
  description: "Explore property values, neighbourhoods, permits and the facts behind your next home with a source-grounded assistant.",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
