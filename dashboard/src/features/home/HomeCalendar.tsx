import { CalendarDays } from 'lucide-react';
import type { Service } from '../../api';
import { Link } from '../../lib/router';
import { CalendarView } from '../calendar/CalendarView';

export function HomeCalendar({ nextcloud }: { nextcloud?: Service }) {
  return (
    <section aria-labelledby="calendar-heading">
      <div className="section-title">
        <h2 id="calendar-heading">
          <CalendarDays /> Calendar
        </h2>
        <Link to="/calendar">Open calendar</Link>
      </div>
      <CalendarView nextcloud={nextcloud} compact />
    </section>
  );
}
