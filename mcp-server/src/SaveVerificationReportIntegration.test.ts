import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import { HwpxDocument } from './HwpxDocument';
import { buildDocumentPreSaveSnapshot, buildHwpxVerificationReport } from './index';

describe('save_document integration with a real sample HWPX', () => {
  const samplePath = '/home/airmanbot/obsidian/OpenClaw/work/hwpx-exam/original-template.hwpx';
  const testDir = path.join(__dirname, '..', 'test-output');
  const workingPath = path.join(testDir, 'save-verification-integration.hwpx');

  beforeEach(() => {
    if (!fs.existsSync(testDir)) fs.mkdirSync(testDir, { recursive: true });
    fs.copyFileSync(samplePath, workingPath);
  });

  afterEach(() => {
    if (fs.existsSync(workingPath)) fs.unlinkSync(workingPath);
  });

  it('saves a copied real sample and produces a clean verification report', async () => {
    const doc = await HwpxDocument.createFromBuffer('integration-real-sample', workingPath, fs.readFileSync(workingPath));
    const paragraphs = doc.getParagraphs(0);
    const targetParagraph = paragraphs.find((p) => typeof p.index === 'number' && p.index >= 0);

    expect(targetParagraph).toBeTruthy();

    doc.updateParagraphText(0, targetParagraph!.index, 0, 'PR-1 integration verification sample');

    const preSaveSnapshot = await buildDocumentPreSaveSnapshot(doc);
    fs.writeFileSync(workingPath, await doc.save());

    const report = await buildHwpxVerificationReport(workingPath, preSaveSnapshot);

    expect(report.ok).toBe(true);
    expect(report.missing_files).toEqual([]);
    expect(report.section_reports.length).toBeGreaterThan(0);
    expect(report.totals.paragraphs).toBeGreaterThan(0);
    expect(report.totals.tables).toBeGreaterThan(0);
    expect(report.totals.suspicious_patterns).toBe(0);
    expect(report.totals.placeholders).toBe(0);
  });
});
