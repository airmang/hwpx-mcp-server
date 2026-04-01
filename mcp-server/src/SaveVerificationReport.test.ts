import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import * as fs from 'fs';
import * as path from 'path';
import JSZip from 'jszip';
import { HwpxDocument } from './HwpxDocument';
import { buildDocumentPreSaveSnapshot, buildHwpxVerificationReport } from './index';

async function createTestHwpx(sectionXml: string): Promise<Buffer> {
  const zip = new JSZip();

  zip.file('mimetype', 'application/hwp+zip');
  zip.file('Contents/content.hpf', `<?xml version="1.0" encoding="UTF-8"?>
<hpf:content xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" version="1.0">
  <hpf:metadata/>
</hpf:content>`);
  zip.file('Contents/header.xml', `<?xml version="1.0" encoding="UTF-8"?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">
  <hh:docInfo><hh:title>Verification Test</hh:title></hh:docInfo>
</hh:head>`);
  zip.file('Contents/section0.xml', sectionXml);
  zip.file('[Content_Types].xml', `<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>`);

  return await zip.generateAsync({ type: 'nodebuffer' });
}

describe('save_document verification report', () => {
  const testDir = path.join(__dirname, '..', 'test-output');
  const sourcePath = path.join(testDir, 'save-verification-source.hwpx');
  const savedPath = path.join(testDir, 'save-verification-saved.hwpx');

  beforeEach(() => {
    if (!fs.existsSync(testDir)) fs.mkdirSync(testDir, { recursive: true });
  });

  afterEach(() => {
    for (const target of [sourcePath, savedPath]) {
      if (fs.existsSync(target)) fs.unlinkSync(target);
    }
  });

  it('reports pre/post count diffs and clean totals for a normal save', async () => {
    const initialSectionXml = `<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p id="1"><hp:run><hp:t>Alpha</hp:t></hp:run></hp:p>
</hs:sec>`;

    fs.writeFileSync(sourcePath, await createTestHwpx(initialSectionXml));
    const doc = await HwpxDocument.createFromBuffer('verify-clean', sourcePath, fs.readFileSync(sourcePath));

    doc.insertParagraph(0, 0, 'Beta');

    const preSaveSnapshot = await buildDocumentPreSaveSnapshot(doc);
    fs.writeFileSync(savedPath, await doc.save());

    const report = await buildHwpxVerificationReport(savedPath, preSaveSnapshot);

    expect(report.ok).toBe(true);
    expect(report.totals.paragraphs).toBe(2);
    expect(report.totals.placeholders).toBe(0);
    expect(report.totals.suspicious_patterns).toBe(0);
    expect(report.diff_summary.paragraphs).toBe(1);
    expect(report.section_reports[0].counts_diff?.paragraphs).toBe(1);
    expect(report.section_reports[0].xml_length).toBeGreaterThan(0);
  });

  it('flags leftover placeholders and suspicious XML/text patterns', async () => {
    const suspiciousSectionXml = `<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p id="1"><hp:run><hp:t>[NAME]</hp:t></hp:run></hp:p>
  <hp:p id="2"><hp:run><hp:t>Tom & Jerry</hp:t></hp:run></hp:p>
</hs:sec>`;

    fs.writeFileSync(savedPath, await createTestHwpx(suspiciousSectionXml));

    const report = await buildHwpxVerificationReport(savedPath);

    expect(report.ok).toBe(false);
    expect(report.totals.placeholders).toBeGreaterThan(0);
    expect(report.totals.suspicious_patterns).toBeGreaterThan(0);
    expect(report.section_reports[0].placeholder_examples).toContain('[NAME]');
    expect(report.warnings.some((warning: string) => warning.includes('placeholder-like tokens remain'))).toBe(true);
    expect(report.warnings.some((warning: string) => warning.includes('suspicious XML/text patterns detected'))).toBe(true);
  });

  it('does not flag common inline HWPX controls, whitespace-only runs, or self-closing hp:t as suspicious', async () => {
    const validInlineSectionXml = `<?xml version="1.0" encoding="UTF-8"?>
<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"
        xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
  <hp:p id="1"><hp:run><hp:t>① 보기<hp:tab width="1200" leader="0" type="1"/>② 정답<hp:fwSpace/></hp:t></hp:run></hp:p>
  <hp:p id="2"><hp:run><hp:t>   </hp:t></hp:run></hp:p>
  <hp:p id="3"><hp:run><hp:t/></hp:run></hp:p>
</hs:sec>`;

    fs.writeFileSync(savedPath, await createTestHwpx(validInlineSectionXml));

    const report = await buildHwpxVerificationReport(savedPath);

    expect(report.ok).toBe(true);
    expect(report.totals.placeholders).toBe(0);
    expect(report.totals.suspicious_patterns).toBe(0);
    expect(report.warnings).toEqual([]);
  });
});
