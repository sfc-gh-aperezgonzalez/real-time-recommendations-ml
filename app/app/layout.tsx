import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'PlayNova — Real-Time Game Recommendations',
  description: 'Personalized game discovery powered by real-time AI',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
