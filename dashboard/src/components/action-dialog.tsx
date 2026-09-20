'use client';
import { Component, type FormEvent } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { X, ShieldCheck } from 'lucide-react';
import { Button } from './ui/button';
import type { CommandDraft } from '@/lib/schema';

export type Action = Readonly<{ title: string; description: string; draft: CommandDraft; confirmation?: string; danger?: boolean }>;
export class ActionDialog extends Component<{ action: Action | null; onClose: () => void; onSubmit: (draft: CommandDraft) => void; disabled: boolean }, { typed: string }> {
  state = { typed: '' };
  componentDidUpdate(previous: Readonly<typeof this.props>) { if (previous.action !== this.props.action && this.state.typed) this.setState({ typed: '' }); }
  private submit = (event: FormEvent) => { event.preventDefault(); const action = this.props.action; if (!action || this.props.disabled || (action.confirmation && this.state.typed !== action.confirmation)) return; this.props.onSubmit(action.draft); this.props.onClose(); };
  render() { const action = this.props.action; return <Dialog.Root open={!!action} onOpenChange={open => { if (!open) this.props.onClose(); }}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="dialog-content"><div className="dialog-icon"><ShieldCheck size={24} /></div><Dialog.Title>{action?.title}</Dialog.Title><Dialog.Description>{action?.description}</Dialog.Description><form onSubmit={this.submit}>{action?.confirmation && <label className="field">Type <strong>{action.confirmation}</strong> to confirm<input autoComplete="off" value={this.state.typed} onChange={event => this.setState({ typed: event.target.value })} /></label>}<p className="muted small">The worker validates this request. Its outcome will appear in the command status panel.</p><div className="dialog-actions"><Button type="button" variant="secondary" onClick={this.props.onClose}>Go back</Button><Button variant={action?.danger ? 'destructive' : 'default'} disabled={this.props.disabled || (!!action?.confirmation && action.confirmation !== this.state.typed)}>{action?.title}</Button></div></form><Dialog.Close className="icon-button dialog-close" aria-label="Close confirmation"><X size={20} /></Dialog.Close></Dialog.Content></Dialog.Portal></Dialog.Root>; }
}
