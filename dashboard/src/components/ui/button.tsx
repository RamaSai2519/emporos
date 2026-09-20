'use client';
import { Component, type ButtonHTMLAttributes } from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cva, type VariantProps } from 'class-variance-authority';
import { Styles } from '@/lib/styles';
const buttonVariants = cva('button', { variants: { variant: { default: 'button-primary', secondary: 'button-secondary', destructive: 'button-danger', ghost: 'button-ghost' }, size: { default: '', sm: 'button-sm' } }, defaultVariants: { variant: 'default', size: 'default' } });
export class Button extends Component<ButtonHTMLAttributes<HTMLButtonElement> & VariantProps<typeof buttonVariants> & { asChild?: boolean }> {
  render() { const { variant, size, asChild, className, ...props } = this.props; const Tag = asChild ? Slot : 'button'; return <Tag className={Styles.cn(buttonVariants({ variant, size }), className)} {...props} />; }
}
