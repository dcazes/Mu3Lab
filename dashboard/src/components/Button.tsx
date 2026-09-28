import { ArrowUpRight, Loader2, type LucideIcon } from 'lucide-react';
import type { AnchorHTMLAttributes, ButtonHTMLAttributes, ReactNode } from 'react';
import { Link } from '../lib/router';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
type Size = 'sm' | 'md';

interface Look {
  variant?: Variant;
  size?: Size;
  icon?: LucideIcon;
}

const classes = (variant: Variant, size: Size, className = '') => `btn btn-${variant} btn-${size} ${className}`.trim();

export function Button({
  variant = 'secondary',
  size = 'md',
  icon: Icon,
  loading = false,
  className,
  children,
  disabled,
  type = 'button',
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & Look & { loading?: boolean }) {
  return (
    <button type={type} className={classes(variant, size, className)} disabled={disabled || loading} {...props}>
      {loading ? <Loader2 className="spin" /> : Icon && <Icon />}
      {children}
    </button>
  );
}

/** Opens another app in a new tab. */
export function ExternalButton({
  variant = 'secondary',
  size = 'md',
  icon: Icon,
  className,
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & Look & { children: ReactNode }) {
  return (
    <a className={classes(variant, size, className)} target="_blank" rel="noreferrer" {...props}>
      {Icon && <Icon />}
      {children}
      <ArrowUpRight className="btn-trailing" />
    </a>
  );
}

export function LinkButton({
  to,
  variant = 'secondary',
  size = 'md',
  icon: Icon,
  className,
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement> & Look & { to: string }) {
  return (
    <Link to={to} className={classes(variant, size, className)} {...props}>
      {Icon && <Icon />}
      {children}
    </Link>
  );
}
