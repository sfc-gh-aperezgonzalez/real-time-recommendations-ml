export interface Player {
  player_id: string;
  display_name: string;
  region_code: string;
  player_segment?: string;
}

const STORAGE_KEY = 'playnova_player';

export function getPlayer(): Player | null {
  if (typeof window === 'undefined') return null;
  const raw = localStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as Player;
  } catch {
    return null;
  }
}

export function setPlayer(player: Player): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(player));
}

export function clearPlayer(): void {
  localStorage.removeItem(STORAGE_KEY);
}

export function getLastPlayed(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('playnova_lastPlayed');
}

export function setLastPlayed(gameId: string): void {
  localStorage.setItem('playnova_lastPlayed', gameId);
}

export function getLastPlayedCategory(): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem('playnova_lastPlayedCategory');
}

export function setLastPlayedCategory(categoryId: string): void {
  localStorage.setItem('playnova_lastPlayedCategory', categoryId);
}
