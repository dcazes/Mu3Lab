import { Card, PageHeader } from '../../components/Layout';
import { type SectionStyle, type ThemePreference, useSectionStyle, useTheme } from '../../lib/theme';

const THEMES: { value: ThemePreference; label: string }[] = [
  { value: 'system', label: 'Match system' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
];

const SECTIONS: { value: SectionStyle; label: string; hint: string }[] = [
  { value: 'framed', label: 'Framed', hint: 'Each section of apps sits in its own frame.' },
  { value: 'open', label: 'Open', hint: 'Sections sit on the page with only their heading between them.' },
];

/** Settings > Appearance: how Mu3Lab looks on this device. Each person and device chooses for itself. */
export function AppearanceSettings() {
  const { preference, setPreference } = useTheme();
  const { style, setStyle } = useSectionStyle();
  return (
    <>
      <PageHeader
        title="Appearance"
        description="How Mu3Lab looks on this device. Other people and devices keep their own choice."
      />
      <Card title="Theme" description="Light, dark, or follow this device's setting.">
        <div className="segmented" role="radiogroup" aria-label="Theme">
          {THEMES.map(({ value, label }) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={preference === value}
              onClick={() => setPreference(value)}
            >
              {label}
            </button>
          ))}
        </div>
      </Card>
      <Card title="App sections" description="How the Apps page draws its categories and stacks.">
        <div className="appearance-choices" role="radiogroup" aria-label="App sections">
          {SECTIONS.map(({ value, label, hint }) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={style === value}
              className="appearance-choice"
              onClick={() => setStyle(value)}
            >
              <span className={`appearance-sample is-${value}`} aria-hidden>
                {[3, 2].map((tiles, group) => (
                  <span key={group} className="appearance-sample-group">
                    <i />
                    <span>
                      {Array.from({ length: tiles }, (_, index) => (
                        <b key={index} />
                      ))}
                    </span>
                  </span>
                ))}
              </span>
              <strong>{label}</strong>
              <small>{hint}</small>
            </button>
          ))}
        </div>
      </Card>
    </>
  );
}
