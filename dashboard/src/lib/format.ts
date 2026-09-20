export class Display {
  static money(value: string | number | null | undefined) { return value == null ? 'Unavailable' : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(Number(value)); }
  static time(value: string) { return new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value)); }
  static date(value: number) { return new Intl.DateTimeFormat('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric' }).format(value); }
  static tone(value: string | number | null | undefined) { return value == null ? 'muted' : Number(value) < 0 ? 'negative' : Number(value) > 0 ? 'positive' : ''; }
}
