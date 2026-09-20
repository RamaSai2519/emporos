import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';
export class Styles { static cn(...values: ClassValue[]) { return twMerge(clsx(values)); } }
