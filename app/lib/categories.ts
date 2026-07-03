// Distinct color per game category so the same-category real-time loop is visually
// obvious on camera. Keyed by CATEGORY_ID (1-12); falls back to the accent color.
export const CATEGORY_COLORS: Record<string, string> = {
  '1': '#7A3FF2',  // Video Slots       - violet
  '2': '#F2C63F',  // Jackpot Slots     - gold
  '3': '#3F9BF2',  // Classic Slots     - blue
  '4': '#3BB54A',  // Table Games       - green
  '5': '#F2673F',  // Scratch & Instant - orange-red
  '6': '#E23F8C',  // Live Roulette     - magenta
  '7': '#2AC2B8',  // Live Blackjack    - teal
  '8': '#9B7BF2',  // Live Baccarat     - lavender
  '9': '#F2A03F',  // Live Game Shows   - amber
  '10': '#4C6EF5', // Sportsbook        - indigo
  '11': '#B54AD6', // Esports           - purple-magenta
  '12': '#6FCF57', // Megaways Slots    - lime
};

export function categoryColor(categoryId?: string | number | null): string {
  if (categoryId == null) return 'var(--accent-primary)';
  return CATEGORY_COLORS[String(categoryId)] ?? 'var(--accent-primary)';
}
