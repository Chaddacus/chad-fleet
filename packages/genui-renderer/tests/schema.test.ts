import { describe, it, expect } from 'vitest';
import { ViewSpecSchema } from '../src/schema.js';

describe('ViewSpecSchema', () => {
  it('accepts a valid spec with a Badge inside a Card', () => {
    const spec = {
      view: [
        {
          primitive: 'Card',
          title: 'Test Card',
          children: [
            { primitive: 'Badge', tone: 'success', label: 'On track' },
          ],
        },
      ],
      narrative: 'All good.',
    };
    const result = ViewSpecSchema.safeParse(spec);
    expect(result.success).toBe(true);
  });

  it('accepts a spec with all primitive types at top level', () => {
    const spec = {
      view: [
        { primitive: 'Badge', tone: 'info', label: 'Info badge' },
        { primitive: 'Stat', label: 'Count', value: 42, delta: '+5', tone: 'success' },
        { primitive: 'Table', headers: ['A', 'B'], rows: [['x', 1], ['y', null]] },
        { primitive: 'Timeline', items: [{ ts: '2024-01-01', label: 'Start', body: 'Kicked off' }] },
        { primitive: 'Chart', kind: 'bar', data: [{ x: 'Jan', y: 10 }, { x: 'Feb', y: 20 }], xLabel: 'Month', yLabel: 'Count' },
      ],
    };
    const result = ViewSpecSchema.safeParse(spec);
    expect(result.success).toBe(true);
  });

  it('accepts a spec without narrative', () => {
    const spec = {
      view: [{ primitive: 'Badge', tone: 'warning', label: 'Watch out' }],
    };
    const result = ViewSpecSchema.safeParse(spec);
    expect(result.success).toBe(true);
  });

  it('rejects a spec with unknown primitive', () => {
    const spec = {
      view: [{ primitive: 'Unicorn', name: 'mystery' }],
    };
    const result = ViewSpecSchema.safeParse(spec);
    expect(result.success).toBe(false);
  });

  it('rejects a Badge with invalid tone', () => {
    const spec = {
      view: [{ primitive: 'Badge', tone: 'purple', label: 'bad' }],
    };
    const result = ViewSpecSchema.safeParse(spec);
    expect(result.success).toBe(false);
  });

  it('rejects a Card missing title', () => {
    const spec = {
      view: [{ primitive: 'Card' }],
    };
    const result = ViewSpecSchema.safeParse(spec);
    expect(result.success).toBe(false);
  });

  it('rejects a Chart with invalid kind', () => {
    const spec = {
      view: [{ primitive: 'Chart', kind: 'pie', data: [] }],
    };
    const result = ViewSpecSchema.safeParse(spec);
    expect(result.success).toBe(false);
  });

  it('accepts a Table with null cells', () => {
    const spec = {
      view: [
        {
          primitive: 'Table',
          headers: ['Name', 'Value'],
          rows: [['Alice', null], ['Bob', 5]],
        },
      ],
    };
    const result = ViewSpecSchema.safeParse(spec);
    expect(result.success).toBe(true);
  });

  it('rejects a spec with no view array', () => {
    const spec = { narrative: 'Missing view' };
    const result = ViewSpecSchema.safeParse(spec);
    expect(result.success).toBe(false);
  });

  it('accepts nested Card children', () => {
    const spec = {
      view: [
        {
          primitive: 'Card',
          title: 'Outer',
          children: [
            {
              primitive: 'Card',
              title: 'Inner',
              children: [
                { primitive: 'Stat', label: 'KPI', value: 99 },
              ],
            },
          ],
        },
      ],
    };
    const result = ViewSpecSchema.safeParse(spec);
    expect(result.success).toBe(true);
  });
});
