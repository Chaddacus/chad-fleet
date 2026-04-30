import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Badge } from '../src/client/primitives/Badge.js';
import { Stat } from '../src/client/primitives/Stat.js';
import { Table } from '../src/client/primitives/Table.js';
import { Timeline } from '../src/client/primitives/Timeline.js';
import { Chart } from '../src/client/primitives/Chart.js';
import { Card } from '../src/client/primitives/Card.js';
import { renderViewNode } from '../src/client/GenView.js';
import type { ViewNode } from '../src/schema.js';

describe('Badge', () => {
  it('renders the label', () => {
    render(<Badge tone="success" label="On track" />);
    expect(screen.getByText('On track')).toBeTruthy();
  });

  it('applies the correct tone class', () => {
    const { container } = render(<Badge tone="error" label="At risk" />);
    const span = container.querySelector('span');
    expect(span?.className).toContain('bg-red-100');
  });

  it.each([
    ['info', 'bg-blue-100'],
    ['success', 'bg-green-100'],
    ['warning', 'bg-yellow-100'],
    ['error', 'bg-red-100'],
  ] as const)('tone %s has class %s', (tone, cls) => {
    const { container } = render(<Badge tone={tone} label="x" />);
    expect(container.querySelector('span')?.className).toContain(cls);
  });
});

describe('Stat', () => {
  it('renders label and value', () => {
    render(<Stat label="Revenue" value={42} />);
    expect(screen.getByText('Revenue')).toBeTruthy();
    expect(screen.getByText('42')).toBeTruthy();
  });

  it('renders delta when provided', () => {
    render(<Stat label="MRR" value="$1,200" delta="+12%" />);
    expect(screen.getByText('+12%')).toBeTruthy();
  });

  it('omits delta when not provided', () => {
    const { container } = render(<Stat label="Count" value={0} />);
    // Should not have a delta span
    const spans = container.querySelectorAll('span');
    const texts = Array.from(spans).map((s) => s.textContent);
    expect(texts.every((t) => t !== undefined && !t.startsWith('+') && !t.startsWith('-'))).toBe(true);
  });
});

describe('Table', () => {
  it('renders headers and rows', () => {
    render(
      <Table
        headers={['Name', 'Status']}
        rows={[
          ['Alice', 'Active'],
          ['Bob', null],
        ]}
      />,
    );
    expect(screen.getByText('Name')).toBeTruthy();
    expect(screen.getByText('Status')).toBeTruthy();
    expect(screen.getByText('Alice')).toBeTruthy();
    expect(screen.getByText('Active')).toBeTruthy();
    // null cells render as em-dash
    expect(screen.getByText('—')).toBeTruthy();
  });

  it('renders empty rows without crashing', () => {
    const { container } = render(<Table headers={['A']} rows={[]} />);
    expect(container.querySelector('tbody')?.children).toHaveLength(0);
  });
});

describe('Timeline', () => {
  it('renders items', () => {
    render(
      <Timeline
        items={[
          { ts: '2024-01-01', label: 'Launch', body: 'We launched!' },
          { ts: '2024-02-01', label: 'Milestone 2' },
        ]}
      />,
    );
    expect(screen.getByText('Launch')).toBeTruthy();
    expect(screen.getByText('We launched!')).toBeTruthy();
    expect(screen.getByText('Milestone 2')).toBeTruthy();
  });

  it('omits body when not provided', () => {
    const { container } = render(
      <Timeline items={[{ ts: '2024-01-01', label: 'Only label' }]} />,
    );
    const listItems = container.querySelectorAll('li');
    expect(listItems).toHaveLength(1);
    // No extra paragraph for body
    expect(listItems[0]?.querySelectorAll('p')).toHaveLength(1);
  });
});

describe('Chart', () => {
  it('renders without crashing (bar)', () => {
    const { container } = render(
      <Chart
        kind="bar"
        data={[
          { x: 'Jan', y: 10 },
          { x: 'Feb', y: 20 },
        ]}
        xLabel="Month"
        yLabel="Count"
      />,
    );
    // recharts renders an SVG
    expect(container.querySelector('.recharts-wrapper') !== null || container.firstChild !== null).toBe(true);
  });

  it('renders without crashing (line)', () => {
    const { container } = render(
      <Chart
        kind="line"
        data={[
          { x: 0, y: 5 },
          { x: 1, y: 15 },
        ]}
      />,
    );
    expect(container.firstChild).not.toBeNull();
  });
});

describe('Card', () => {
  it('renders title', () => {
    render(<Card title="My Card" renderNode={renderViewNode} />);
    expect(screen.getByText('My Card')).toBeTruthy();
  });

  it('renders subtitle when provided', () => {
    render(<Card title="T" subtitle="Sub" renderNode={renderViewNode} />);
    expect(screen.getByText('Sub')).toBeTruthy();
  });

  it('renders children via renderNode', () => {
    const children: ViewNode[] = [
      { primitive: 'Badge', tone: 'info', label: 'Child badge' },
    ];
    render(<Card title="T" children={children} renderNode={renderViewNode} />);
    expect(screen.getByText('Child badge')).toBeTruthy();
  });

  it('applies tone border class', () => {
    const { container } = render(
      <Card title="T" tone="error" renderNode={renderViewNode} />,
    );
    const div = container.querySelector('div');
    expect(div?.className).toContain('border-red-300');
  });
});

describe('renderViewNode', () => {
  it('returns null for unknown primitives', () => {
    // Force an unknown node via cast
    const result = renderViewNode({ primitive: 'Unknown' } as unknown as ViewNode);
    expect(result).toBeNull();
  });

  it('routes each primitive correctly', () => {
    const cases: ViewNode[] = [
      { primitive: 'Badge', tone: 'info', label: 'B' },
      { primitive: 'Stat', label: 'L', value: 1 },
      { primitive: 'Table', headers: ['H'], rows: [['R']] },
      { primitive: 'Timeline', items: [{ ts: 't', label: 'l' }] },
      { primitive: 'Chart', kind: 'bar', data: [{ x: 1, y: 1 }] },
      { primitive: 'Card', title: 'C' },
    ];

    for (const node of cases) {
      const result = renderViewNode(node);
      expect(result).not.toBeNull();
    }
  });
});
