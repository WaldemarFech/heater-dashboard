"""Pure calculations; unknown periods and resets are never disguised as zero."""
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

def validate_turn_on(state, online, age_seconds, pending):
    return state == 'OFF' and online is True and age_seconds is not None and 0 <= age_seconds <= 25 and not pending

def normalize_events(rows):
    """Preserve original timestamps, especially the observation before start."""
    events=[]
    for ts,topic,payload in rows:
        if payload in ('ON','OFF','Online','Offline'):
            events.append((ts,payload,'lwt' if topic.endswith('/LWT') else 'power'))
    return events

def normalize_status_samples(rows):
    """Status.Power is a single-relay bitmask: accept only known 0/1 values."""
    return [(ts,'ON' if value==1 else 'OFF','power') for ts,topic,value in rows
            if type(value) in (int,float) and value in (0,1)]


def calculate_intervals(start, end, events, observation_times):
    """Bound state claims by pairs of archive heartbeats at most 600 s apart.

    Before/after such observed coverage and across gaps, the state is unknown.
    A gap invalidates the previous state; unobserved power events cannot restore
    it. Times prior to the requested window may establish the boundary state
    only if they lie within the same continuous observed coverage.
    """
    observations=sorted(set(t for t in observation_times if t<=end))
    coverage=[]
    for a,b in zip(observations,observations[1:]):
        if (b-a).total_seconds()<=600 and b>=start and a<end:
            if coverage and coverage[-1][1]==a:
                coverage[-1]=(coverage[-1][0],b)
            else: coverage.append((a,b))
    points=[]
    for a,b in coverage:
        points.append((a,0,'coverage','start'))
        points.append((b,0,'coverage','end'))
    for at,value,kind in events:
        if at<=end: points.append((at,1,kind,value))
    points.sort(key=lambda x:(x[0],x[1]))
    state='unknown';active=False;cursor=start;starts=0;segments=[]
    def append(a,b,value):
        a=max(start,a);b=min(end,b)
        if b<=a:return
        if segments and segments[-1][2]==value and segments[-1][1]==a:
            segments[-1]=(segments[-1][0],b,value)
        else: segments.append((a,b,value))
    for at,_,kind,value in points:
        if at>cursor:append(cursor,at,state if active else 'unknown')
        cursor=max(cursor,at)
        if kind=='coverage':
            active=value=='start';state='unknown'
        elif active and kind=='lwt' and value in ('Offline','Online'):
            state='unknown'
        elif active and kind=='power' and value in ('ON','OFF'):
            if value=='ON' and state=='OFF' and at>=start:starts+=1
            state=value
    append(cursor,end,state if active else 'unknown')
    totals={'ON':0,'OFF':0,'unknown':0}
    for a,b,value in segments:totals[value]+=(b-a).total_seconds()
    return {'segments':[{'start':a.isoformat(),'end':b.isoformat(),'state':value} for a,b,value in segments],
            'on_seconds':totals['ON'],'unknown_seconds':totals['unknown'],'starts':starts}

def energy_delta(values):
    if len(values)<2: return {'kwh':None,'reset':False}
    if any(b < a for a,b in zip(values,values[1:])): return {'kwh':None,'reset':True}
    return {'kwh':round(values[-1]-values[0],6),'reset':False}

def merge_energy_topics(rows):
    """Configured topics are aliases of one physical total counter.

    Equal counter readings at migration are expected. A backwards step is an
    ambiguity/reset, never negative or silently added energy. Long gaps are
    reported even if the counter remains monotonic, since a reset might be hidden.
    """
    rows=sorted(rows,key=lambda r:r[0])
    result=energy_delta([float(row[2]) for row in rows])
    result['gap_count']=sum((b[0]-a[0]).total_seconds()>600 for a,b in zip(rows,rows[1:]))
    result['topic_count']=len({row[1] for row in rows})
    return result


def history_window(days, until, now):
    """Return an archived local-calendar window, with the current day capped at now."""
    if not until:
        return now-timedelta(days=days),now
    try:
        date=datetime.strptime(until,'%Y-%m-%d').date()
        if date.isoformat()!=until: raise ValueError('invalid date')
        local=ZoneInfo('Europe/Berlin')
        start=datetime.combine(date-timedelta(days=days-1),time.min,tzinfo=local).astimezone(timezone.utc)
        end=datetime.combine(date+timedelta(days=1),time.min,tzinfo=local).astimezone(timezone.utc)
        if start>=now: raise ValueError('future date')
        return start,min(end,now)
    except (ValueError,OverflowError):
        raise ValueError('invalid history date') from None
