import { ChevronRight, type LucideIcon } from 'lucide-react';
import type { ReactNode } from 'react';
import { Link, usePath } from '../lib/router';

export function PageHeader({
  title,
  description,
  actions,
  breadcrumb,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  breadcrumb?: { label: string; to: string };
}) {
  return (
    <header className="page-header">
      <div className="page-header-text">
        {breadcrumb && (
          <Link to={breadcrumb.to} className="breadcrumb">
            {breadcrumb.label}
            <ChevronRight />
          </Link>
        )}
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-header-actions">{actions}</div>}
    </header>
  );
}

export function Card({
  title,
  description,
  actions,
  children,
  className = '',
  flush = false,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
  flush?: boolean;
}) {
  return (
    <section className={`card ${flush ? 'card-flush' : ''} ${className}`.trim()}>
      {(title || actions) && (
        <header className="card-header">
          <div>
            {title && <h2>{title}</h2>}
            {description && <p>{description}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
}: {
  icon?: LucideIcon;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty">
      {Icon && <Icon className="empty-icon" />}
      <h3>{title}</h3>
      {children && <p>{children}</p>}
      {action}
    </div>
  );
}

export function Facts({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="facts">
      {items.map((item) => (
        <div key={item.label}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Callout({
  tone = 'info',
  icon: Icon,
  title,
  children,
  action,
}: {
  tone?: 'info' | 'warning' | 'danger' | 'success';
  icon?: LucideIcon;
  title: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className={`callout callout-${tone}`} role={tone === 'danger' ? 'alert' : undefined}>
      {Icon && <Icon className="callout-icon" />}
      <div className="callout-text">
        <b>{title}</b>
        {children && <p>{children}</p>}
      </div>
      {action && <div className="callout-action">{action}</div>}
    </div>
  );
}

export interface TabItem {
  label: string;
  to: string;
  count?: number;
}

/** Route-backed tabs; the active tab is the longest matching prefix. */
export function Tabs({ items, label }: { items: TabItem[]; label: string }) {
  const path = usePath();
  const active = [...items].sort((a, b) => b.to.length - a.to.length).find((item) => path.startsWith(item.to));
  return (
    <nav className="tabs" aria-label={label}>
      {items.map((item) => (
        <Link
          key={item.to}
          to={item.to}
          className={item === active ? 'active' : ''}
          aria-current={item === active ? 'page' : undefined}
        >
          {item.label}
          {item.count !== undefined && <span className="tab-count">{item.count}</span>}
        </Link>
      ))}
    </nav>
  );
}

export function Collapsible({ title, children }: { title: string; children: ReactNode }) {
  return (
    <details className="collapsible">
      <summary>
        <ChevronRight />
        {title}
      </summary>
      <div className="collapsible-body">{children}</div>
    </details>
  );
}
