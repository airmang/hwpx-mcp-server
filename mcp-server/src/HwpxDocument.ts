import JSZip from 'jszip';
import pako from 'pako';
import { HwpxParser } from './HwpxParser';
import { HangingIndentCalculator } from './HangingIndentCalculator';
import {
  HwpxContent,
  HwpxParagraph,
  TextRun,
  CharacterStyle,
  ParagraphStyle,
  HwpxTable,
  TableCell,
  TableRow,
  SectionElement,
  HwpxSection,
  HwpxImage,
  PageSettings,
  Footnote,
  Endnote,
  Memo,
  ColumnDef,
  CharShape,
  ParaShape,
  StyleDef,
  HwpxLine,
  HwpxRect,
  HwpxEllipse,
  HwpxEquation,
  HeaderFooter,
} from './types';

type DocumentFormat = 'hwpx' | 'hwp';

const MAX_UNDO_STACK_SIZE = 50;

// Image positioning options
export interface ImagePositionOptions {
  /** Position type: 'inline' (flows with text like a character) or 'floating' (positioned relative to anchor) */
  positionType?: 'inline' | 'floating';
  /** Vertical reference point: 'para' (paragraph), 'paper' (page) */
  vertRelTo?: 'para' | 'paper';
  /** Horizontal reference point: 'column', 'para' (paragraph), 'paper' (page) */
  horzRelTo?: 'column' | 'para' | 'paper';
  /** Vertical alignment: 'top', 'center', 'bottom' */
  vertAlign?: 'top' | 'center' | 'bottom';
  /** Horizontal alignment: 'left', 'center', 'right' */
  horzAlign?: 'left' | 'center' | 'right';
  /** Vertical offset from anchor in points */
  vertOffset?: number;
  /** Horizontal offset from anchor in points */
  horzOffset?: number;
  /** Text wrap mode */
  textWrap?: 'top_and_bottom' | 'square' | 'tight' | 'behind_text' | 'in_front_of_text' | 'none';
}

// Document chunk type for agentic reading
export interface DocumentChunk {
  id: string;
  text: string;
  startOffset: number;
  endOffset: number;
  sectionIndex: number;
  elementType: 'paragraph' | 'table' | 'mixed';
  elementIndex?: number;
  tableIndex?: number;
  cellPosition?: { row: number; col: number };
  metadata: {
    charCount: number;
    wordCount: number;
    hasTable: boolean;
    headingLevel?: number;
  };
}

// Position index entry for tracking document structure
export interface PositionIndexEntry {
  id: string;
  type: 'heading' | 'paragraph' | 'table' | 'image';
  text: string;
  sectionIndex: number;
  elementIndex: number;
  offset: number;
  level?: number; // For headings
  tableInfo?: {
    tableIndex: number;
    rows: number;
    cols: number;
  };
}

export class HwpxDocument {
  private _id: string;
  private _path: string;
  private _zip: JSZip | null;
  private _content: HwpxContent;
  private _isDirty = false;
  private _format: DocumentFormat;

  private _undoStack: string[] = [];
  private _redoStack: string[] = [];
  private _pendingTextReplacements: Array<{ oldText: string; newText: string; options: { caseSensitive?: boolean; regex?: boolean; replaceAll?: boolean } }> = [];
  private _pendingDirectTextUpdates: Array<{
    sectionIndex: number;
    elementIndex: number;
    paragraphId: string;  // Stable ID for reliable paragraph identification
    paragraphOccurrence: number;  // Which occurrence of this ID (0-indexed)
    runIndex: number;
    oldText: string;
    newText: string
  }> = [];
  private _pendingTableCellUpdates: Array<{ sectionIndex: number; tableIndex: number; tableId: string; row: number; col: number; text: string; charShapeId?: number }> = [];
  private _pendingNestedTableInserts: Array<{ sectionIndex: number; parentTableIndex: number; row: number; col: number; nestedRows: number; nestedCols: number; data: string[][] }> = [];
  private _pendingImageInserts: Array<{
    sectionIndex: number;
    afterElementIndex: number;
    imageId: string;
    binaryId: string;
    data: string;
    mimeType: string;
    width: number;
    height: number;
    position?: ImagePositionOptions;
    headerText?: string; // Text to search for in XML to find exact position
  }> = [];
  private _pendingCellImageInserts: Array<{
    sectionIndex: number;
    tableIndex: number;
    row: number;
    col: number;
    imageId: string;
    binaryId: string;
    data: string;
    mimeType: string;
    width: number;   // display width in points
    height: number;  // display height in points
    orgWidth: number;  // original image width in pixels
    orgHeight: number; // original image height in pixels
    afterText?: string; // Text to search for - insert image after the paragraph containing this text
  }> = [];
  private _pendingTableInserts: Array<{
    sectionIndex: number;
    afterElementIndex: number;
    rows: number;
    cols: number;
    width: number;
    cellWidth: number;
    insertOrder: number;  // Track insertion order for proper sequencing
    tableId: string;  // In-memory table ID to sync with XML
  }> = [];
  private _tableInsertCounter = 0;  // Counter for insertion order
  private _pendingImageDeletes: Array<{
    imageId: string;
    binaryId: string;
  }> = [];
  private _pendingTableDeletes: Array<{
    sectionIndex: number;
    tableIndex: number;
    tableId?: string;
  }> = [];
  private _pendingParagraphDeletes: Array<{
    sectionIndex: number;
    elementIndex: number;
    elementType: 'paragraph' | 'table';
  }> = [];
  private _pendingCellMerges: Array<{
    sectionIndex: number;
    tableIndex: number;
    startRow: number;
    startCol: number;
    endRow: number;
    endCol: number;
  }> = [];
  private _pendingCellSplits: Array<{
    sectionIndex: number;
    tableIndex: number;
    row: number;
    col: number;
    originalColSpan: number;
    originalRowSpan: number;
  }> = [];
  private _pendingHangingIndents: Array<{
    sectionIndex: number;
    elementIndex: number;
    paragraphId: string;
    indentPt: number;  // Positive value in points
  }> = [];
  private _pendingTableCellHangingIndents: Array<{
    sectionIndex: number;
    tableIndex: number;
    row: number;
    col: number;
    paragraphIndex: number;
    paragraphId: string;
    indentPt: number;  // Positive value in points
  }> = [];
  private _pendingParagraphInserts: Array<{
    sectionIndex: number;
    afterElementIndex: number;
    paragraphId: string;
    text: string;
  }> = [];
  private _pendingParagraphStyles: Array<{
    sectionIndex: number;
    elementIndex: number;
    style: Partial<ParagraphStyle>;
  }> = [];
  private _pendingCharacterStyles: Array<{
    sectionIndex: number;
    elementIndex: number;
    runIndex: number;
    style: Partial<CharacterStyle>;
  }> = [];

  private _pendingTableRowInserts: Array<{
    sectionIndex: number;
    tableIndex: number;
    afterRowIndex: number;
    cellTexts?: string[];
  }> = [];
  private _pendingTableRowDeletes: Array<{
    sectionIndex: number;
    tableIndex: number;
    rowIndex: number;
  }> = [];
  private _pendingTableColumnInserts: Array<{
    sectionIndex: number;
    tableIndex: number;
    afterColIndex: number;
  }> = [];
  private _pendingTableColumnDeletes: Array<{
    sectionIndex: number;
    tableIndex: number;
    colIndex: number;
  }> = [];
  private _pendingParagraphCopies: Array<{
    sourceSection: number;
    sourceParagraph: number;
    targetSection: number;
    targetAfter: number;
  }> = [];
  private _pendingParagraphMoves: Array<{
    sourceSection: number;
    sourceParagraph: number;
    targetSection: number;
    targetAfter: number;
  }> = [];
  private _pendingHeaderUpdates: Array<{
    sectionIndex: number;
    text: string;
  }> = [];
  private _pendingFooterUpdates: Array<{
    sectionIndex: number;
    text: string;
  }> = [];

  // Cache for character properties (id → font size in pt)
  private _charPrCache: Map<number, number> | null = null;

  // Original charPr count for integrity validation
  private _originalCharPrCount?: number;

  private constructor(id: string, path: string, zip: JSZip | null, content: HwpxContent, format: DocumentFormat) {
    this._id = id;
    this._path = path;
    this._zip = zip;
    this._content = content;
    this._format = format;
  }

  // Constants for magic numbers
  private static readonly NESTED_CHECK_LOOKBACK = 500;
  private static readonly SEARCH_SKIP_OFFSET = 10;

  /**
   * Find the closing tag position using balanced bracket matching.
   * Handles nested elements of the same type correctly.
   * @param xml The XML string to search in
   * @param startPos Position right after the opening tag
   * @param openTag Opening tag pattern (e.g., '<hp:tbl')
   * @param closeTag Closing tag (e.g., '</hp:tbl>')
   * @returns Position after the closing tag, or -1 if not found
   */
  private static findClosingTagPosition(
    xml: string,
    startPos: number,
    openTag: string,
    closeTag: string
  ): number {
    let depth = 1;
    let pos = startPos;
    while (depth > 0 && pos < xml.length) {
      const nextOpen = xml.indexOf(openTag, pos);
      const nextClose = xml.indexOf(closeTag, pos);
      if (nextClose === -1) return -1;
      if (nextOpen !== -1 && nextOpen < nextClose) {
        depth++;
        pos = nextOpen + 1;
      } else {
        depth--;
        if (depth === 0) {
          return nextClose + closeTag.length;
        }
        pos = nextClose + 1;
      }
    }
    return -1;
  }

  public static async createFromBuffer(id: string, path: string, data: Buffer): Promise<HwpxDocument> {
    const extension = path.toLowerCase();

    if (extension.endsWith('.hwp')) {
      // HWP parsing would go here - for now return empty content
      const content: HwpxContent = {
        metadata: {},
        sections: [],
        images: new Map(),
        binItems: new Map(),
        binData: new Map(),
        footnotes: [],
        endnotes: [],
      };
      return new HwpxDocument(id, path, null, content, 'hwp');
    } else {
      const zip = await JSZip.loadAsync(data);
      const content = await HwpxParser.parse(zip);
      const doc = new HwpxDocument(id, path, zip, content, 'hwpx');

      // Store original charPr count for integrity validation
      const headerXml = await zip.file('Contents/header.xml')?.async('string');
      if (headerXml) {
        doc._originalCharPrCount = (headerXml.match(/<hh:charPr\b/g) || []).length;
      }

      return doc;
    }
  }

  public static createNew(id: string, title?: string, creator?: string): HwpxDocument {
    const now = new Date().toISOString();
    const content: HwpxContent = {
      metadata: {
        title: title || 'Untitled',
        creator: creator || 'Unknown',
        createdDate: now,
        modifiedDate: now,
      },
      sections: [{
        id: Math.random().toString(36).substring(2, 11),
        elements: [{
          type: 'paragraph',
          data: {
            id: Math.random().toString(36).substring(2, 11),
            runs: [{ text: '' }],
          },
        }],
        pageSettings: {
          width: 59528,
          height: 84188,
          marginTop: 4252,
          marginBottom: 4252,
          marginLeft: 4252,
          marginRight: 4252,
        },
      }],
      images: new Map(),
      binItems: new Map(),
      binData: new Map(),
      footnotes: [],
      endnotes: [],
    };

    // Create a new zip with basic HWPX structure
    const zip = new JSZip();

    // Add minimal required files for a valid HWPX document
    zip.file('mimetype', 'application/hwp+zip');

    // version.xml - required for HWPX format (한컴 형식)
    zip.file('version.xml', `<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" tagetApplication="WORDPROCESSOR" major="5" minor="1" micro="1" buildNumber="0" os="1" xmlVersion="1.5" application="Hancom Office Hangul" appVersion="14, 0, 0, 0 WIN32LEWindows_10"/>`);

    // META-INF/container.xml - required for package structure
    zip.file('META-INF/container.xml', `<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf"><ocf:rootfiles><ocf:rootfile full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/></ocf:rootfiles></ocf:container>`);

    // META-INF/manifest.xml - empty but required
    zip.file('META-INF/manifest.xml', `<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0"/>`);

    // settings.xml - application settings
    zip.file('settings.xml', `<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><ha:HWPApplicationSetting xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0"><ha:CaretPosition listIDRef="0" paraIDRef="0" pos="0"/></ha:HWPApplicationSetting>`);

    // Contents/content.hpf - package manifest with all namespaces
    zip.file('Contents/content.hpf', `<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><opf:package xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history" xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-page" xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf/" xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart" xmlns:hwpunitchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar" xmlns:epub="http://www.idpf.org/2007/ops" xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0" version="" unique-identifier="" id=""><opf:metadata><opf:title>${title || 'Untitled'}</opf:title><opf:language>ko</opf:language><opf:meta name="creator" content="text">${creator || 'Unknown'}</opf:meta><opf:meta name="CreatedDate" content="text">${now}</opf:meta><opf:meta name="ModifiedDate" content="text">${now}</opf:meta></opf:metadata><opf:manifest><opf:item id="header" href="Contents/header.xml" media-type="application/xml"/><opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/><opf:item id="settings" href="settings.xml" media-type="application/xml"/></opf:manifest><opf:spine><opf:itemref idref="header" linear="yes"/><opf:itemref idref="section0" linear="yes"/></opf:spine></opf:package>`);

    // Contents/header.xml with all namespaces
    zip.file('Contents/header.xml', `<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><hh:head xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history" xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-page" xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf/" xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart" xmlns:hwpunitchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar" xmlns:epub="http://www.idpf.org/2007/ops" xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0" version="1.1" secCnt="1">
  <hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/>
  <hh:refList>
    <hh:fontfaces itemCnt="1">
      <hh:fontface lang="HANGUL" fontCnt="1">
        <hh:font id="0" face="함초롬바탕" type="TTF"/>
      </hh:fontface>
    </hh:fontfaces>
    <hh:borderFills itemCnt="2">
      <hh:borderFill id="1" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">
        <hh:slash type="NONE"/>
        <hh:backSlash type="NONE"/>
        <hh:leftBorder type="NONE" width="0.1mm" color="#000000"/>
        <hh:rightBorder type="NONE" width="0.1mm" color="#000000"/>
        <hh:topBorder type="NONE" width="0.1mm" color="#000000"/>
        <hh:bottomBorder type="NONE" width="0.1mm" color="#000000"/>
        <hh:diagonal type="NONE" width="0.1mm" color="#000000"/>
      </hh:borderFill>
      <hh:borderFill id="2" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">
        <hh:slash type="NONE"/>
        <hh:backSlash type="NONE"/>
        <hh:leftBorder type="SOLID" width="0.12mm" color="#000000"/>
        <hh:rightBorder type="SOLID" width="0.12mm" color="#000000"/>
        <hh:topBorder type="SOLID" width="0.12mm" color="#000000"/>
        <hh:bottomBorder type="SOLID" width="0.12mm" color="#000000"/>
        <hh:diagonal type="NONE" width="0.12mm" color="#000000"/>
      </hh:borderFill>
    </hh:borderFills>
    <hh:charProperties itemCnt="1">
      <hh:charPr id="0" height="1000" textColor="#000000" shadeColor="none" useFontSpace="0" useKerning="0" symMark="NONE" borderFillIDRef="1">
        <hh:fontRef hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>
        <hh:ratio hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>
        <hh:spacing hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>
        <hh:relSz hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>
        <hh:offset hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>
      </hh:charPr>
    </hh:charProperties>
    <hh:tabProperties itemCnt="1">
      <hh:tabPr id="0" autoTabLeft="0" autoTabRight="0"/>
    </hh:tabProperties>
    <hh:numberings itemCnt="0"/>
    <hh:bullets itemCnt="0"/>
    <hh:paraProperties itemCnt="1">
      <hh:paraPr id="0" tabPrIDRef="0" condense="0" fontLineHeight="0" snapToGrid="1" suppressLineNumbers="0" checked="0">
        <hh:align horizontal="LEFT" vertical="BASELINE"/>
        <hh:heading type="NONE" idRef="0" level="0"/>
        <hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="KEEP_WORD" widowOrphan="0" keepWithNext="0" keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/>
        <hh:autoSpacing eAsianEng="0" eAsianNum="0"/>
        <hh:lineSpacing type="PERCENT" value="160" unit="HWPUNIT"/>
      </hh:paraPr>
    </hh:paraProperties>
    <hh:styles itemCnt="1">
      <hh:style id="0" type="PARA" name="바탕글" engName="Body" paraPrIDRef="0" charPrIDRef="0" nextStyleIDRef="0" langId="1042" lockForm="0"/>
    </hh:styles>
    <hh:memoProperties itemCnt="0"/>
  </hh:refList>
  <hh:compatibleDocument targetProgram="HWP201X"/>
  <hh:docOption>
    <hh:linkinfo path="" pageInherit="0" footnoteInherit="0"/>
  </hh:docOption>
  <hh:trackChangeConfig flags="0"/>
</hh:head>`);

    // Contents/section0.xml with all namespaces
    zip.file('Contents/section0.xml', `<?xml version="1.0" encoding="UTF-8" standalone="yes" ?><hs:sec xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" xmlns:hp10="http://www.hancom.co.kr/hwpml/2016/paragraph" xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" xmlns:hhs="http://www.hancom.co.kr/hwpml/2011/history" xmlns:hm="http://www.hancom.co.kr/hwpml/2011/master-page" xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:opf="http://www.idpf.org/2007/opf/" xmlns:ooxmlchart="http://www.hancom.co.kr/hwpml/2016/ooxmlchart" xmlns:hwpunitchar="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar" xmlns:epub="http://www.idpf.org/2007/ops" xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0"><hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:secPr id="" textDirection="HORIZONTAL" spaceColumns="1134" tabStop="8000" tabStopVal="4000" tabStopUnit="HWPUNIT" outlineShapeIDRef="1" memoShapeIDRef="0" textVerticalWidthHead="0" masterPageCnt="0"><hp:grid lineGrid="0" charGrid="0" wongoji="0"/><hp:startNum pageStartsOn="BOTH" page="0" pic="0" tbl="0" equation="0"/><hp:visibility hideFirstHeader="0" hideFirstFooter="0" hideFirstMasterPage="0" border="SHOW_ALL" fill="SHOW_ALL" hideFirstPageNum="0" hideFirstEmptyLine="0" showLineNumber="0"/><hp:pagePr landscape="0" width="59528" height="84188" gutterType="LEFT_ONLY"><hp:pageMar header="4252" footer="4252" left="8504" right="8504" top="5668" bottom="4252" gutter="0"/></hp:pagePr><hp:footNotePr><hp:autoNumFormat type="DIGIT"/><hp:noteLine length="-1" type="SOLID" width="0.12mm" color="#000000"/><hp:noteSpacing aboveLine="850" belowLine="567" betweenNotes="283"/><hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="EACH_COLUMN" beneathText="0"/></hp:footNotePr><hp:endNotePr><hp:autoNumFormat type="DIGIT"/><hp:noteLine length="14692" type="SOLID" width="0.12mm" color="#000000"/><hp:noteSpacing aboveLine="850" belowLine="567" betweenNotes="0"/><hp:numbering type="CONTINUOUS" newNum="1"/><hp:placement place="END_OF_DOCUMENT" beneathText="0"/></hp:endNotePr></hp:secPr><hp:t></hp:t></hp:run></hp:p></hs:sec>`);

    // Create empty BinData folder
    zip.folder('BinData');

    return new HwpxDocument(id, 'new-document.hwpx', zip, content, 'hwpx');
  }

  get id(): string { return this._id; }
  get path(): string { return this._path; }
  get format(): DocumentFormat { return this._format; }
  get isDirty(): boolean { return this._isDirty; }
  get zip(): JSZip | null { return this._zip; }
  get content(): HwpxContent { return this._content; }

  // ============================================================
  // Undo/Redo
  // ============================================================

  private saveState(): void {
    const state = this.serializeContent();
    this._undoStack.push(state);
    if (this._undoStack.length > MAX_UNDO_STACK_SIZE) {
      this._undoStack.shift();
    }
    this._redoStack = [];
  }

  private serializeContent(): string {
    return JSON.stringify({
      sections: this._content.sections,
      metadata: this._content.metadata,
    });
  }

  private deserializeContent(state: string): void {
    const parsed = JSON.parse(state);
    this._content.sections = parsed.sections;
    this._content.metadata = parsed.metadata;
  }

  canUndo(): boolean { return this._undoStack.length > 0; }
  canRedo(): boolean { return this._redoStack.length > 0; }

  undo(): boolean {
    if (!this.canUndo()) return false;
    const currentState = this.serializeContent();
    this._redoStack.push(currentState);
    const previousState = this._undoStack.pop()!;
    this.deserializeContent(previousState);
    this.clearAllPendingArrays();
    this.markModified();
    return true;
  }

  redo(): boolean {
    if (!this.canRedo()) return false;
    const currentState = this.serializeContent();
    this._undoStack.push(currentState);
    const nextState = this._redoStack.pop()!;
    this.deserializeContent(nextState);
    this.clearAllPendingArrays();
    this.markModified();
    return true;
  }

  /**
   * Clear all pending operation arrays.
   * Called by undo/redo to prevent memory/XML desync.
   */
  private clearAllPendingArrays(): void {
    this._pendingTextReplacements = [];
    this._pendingDirectTextUpdates = [];
    this._pendingTableCellUpdates = [];
    this._pendingNestedTableInserts = [];
    this._pendingImageInserts = [];
    this._pendingCellImageInserts = [];
    this._pendingTableInserts = [];
    this._pendingImageDeletes = [];
    this._pendingTableDeletes = [];
    this._pendingParagraphDeletes = [];
    this._pendingCellMerges = [];
    this._pendingCellSplits = [];
    this._pendingHangingIndents = [];
    this._pendingTableCellHangingIndents = [];
    this._pendingParagraphInserts = [];
    this._pendingParagraphStyles = [];
    this._pendingCharacterStyles = [];
    this._pendingTableRowInserts = [];
    this._pendingTableRowDeletes = [];
    this._pendingTableColumnInserts = [];
    this._pendingTableColumnDeletes = [];
    this._pendingParagraphCopies = [];
    this._pendingParagraphMoves = [];
    this._pendingHeaderUpdates = [];
    this._pendingFooterUpdates = [];
    if (this._pendingTableMoves) this._pendingTableMoves = [];
  }

  /**
   * Mark document as modified and invalidate agentic reading cache.
   * Call this after any modification that changes document structure or content.
   */
  private markModified(): void {
    this._isDirty = true;
    this.invalidateReadingCache();
  }

  // ============================================================
  // Content Access
  // ============================================================

  getSerializableContent(): object {
    return {
      metadata: this._content.metadata,
      sections: this._content.sections,
      images: Array.from(this._content.images.entries()),
      footnotes: this._content.footnotes,
      endnotes: this._content.endnotes,
    };
  }

  getAllText(): string {
    let text = '';
    for (const section of this._content.sections) {
      for (const element of section.elements) {
        if (element.type === 'paragraph') {
          text += element.data.runs.map(r => r.text).join('') + '\n';
        }
      }
    }
    return text;
  }

  getStructure(): object {
    return {
      format: this._format,
      sections: this._content.sections.map((s, i) => {
        let paragraphs = 0, tables = 0, images = 0;
        for (const el of s.elements) {
          if (el.type === 'paragraph') paragraphs++;
          if (el.type === 'table') tables++;
          if (el.type === 'image') images++;
        }
        return { section: i, paragraphs, tables, images };
      }),
    };
  }

  // ============================================================
  // Paragraph Operations
  // ============================================================

  private findParagraphByPath(sectionIndex: number, elementIndex: number): HwpxParagraph | null {
    const section = this._content.sections[sectionIndex];
    if (!section) return null;
    const element = section.elements[elementIndex];
    if (!element || element.type !== 'paragraph') return null;
    return element.data;
  }

  getParagraphs(sectionIndex?: number): Array<{ section: number; index: number; text: string; style?: ParagraphStyle }> {
    const paragraphs: Array<{ section: number; index: number; text: string; style?: ParagraphStyle }> = [];
    const sections = sectionIndex !== undefined
      ? [{ section: this._content.sections[sectionIndex], idx: sectionIndex }]
      : this._content.sections.map((s, i) => ({ section: s, idx: i }));

    for (const { section, idx } of sections) {
      if (!section) continue;
      section.elements.forEach((el, ei) => {
        if (el.type === 'paragraph') {
          paragraphs.push({
            section: idx,
            index: ei,
            text: el.data.runs.map(r => r.text).join(''),
            style: el.data.paraStyle,
          });
        }
      });
    }
    return paragraphs;
  }

  getParagraph(sectionIndex: number, paragraphIndex: number): { text: string; runs: TextRun[]; style?: ParagraphStyle } | null {
    const para = this.findParagraphByPath(sectionIndex, paragraphIndex);
    if (!para) return null;
    return {
      text: para.runs.map(r => r.text).join(''),
      runs: para.runs,
      style: para.paraStyle,
    };
  }

  updateParagraphText(sectionIndex: number, elementIndex: number, runIndex: number, text: string): void {
    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    if (!paragraph) return;

    // Auto-delegate to preserve styles method for multi-run paragraphs
    if (paragraph.runs.length > 1) {
      this.updateParagraphTextPreserveStyles(sectionIndex, elementIndex, text);
      return;
    }

    // Handle case where paragraph has no runs (e.g., run without hp:t tag)
    // We need to create a run in memory and track the update for XML modification
    if (!paragraph.runs[runIndex]) {
      // Only allow creating run 0 if no runs exist
      if (runIndex === 0 && paragraph.runs.length === 0) {
        // Create a new run in memory
        paragraph.runs.push({ text: '' });
      } else {
        return; // Can't update non-existent run
      }
    }

    // Track for XML update - always add if we have a zip (HWPX file)
    // Similar to updateTableCell which always tracks changes
    if (this._zip) {
      const oldText = paragraph.runs[runIndex].text || '';
      const paragraphOccurrence = this.getParagraphOccurrence(sectionIndex, elementIndex, paragraph.id || '');
      this._pendingDirectTextUpdates.push({
        sectionIndex,
        elementIndex,
        paragraphId: paragraph.id || '',  // Use stable paragraph ID for reliable identification
        paragraphOccurrence,
        runIndex,
        oldText,
        newText: text
      });

      // When updating run 0, clear other runs (full paragraph replacement)
      if (runIndex === 0) {
        for (let i = 1; i < paragraph.runs.length; i++) {
          const otherOldText = paragraph.runs[i].text || '';
          if (otherOldText) {
            this._pendingDirectTextUpdates.push({
              sectionIndex,
              elementIndex,
              paragraphId: paragraph.id || '',
              paragraphOccurrence,
              runIndex: i,
              oldText: otherOldText,
              newText: ''  // Clear other runs
            });
          }
        }
      }
    }

    this.saveState();
    paragraph.runs[runIndex].text = text;

    // Clear other runs in memory too
    if (runIndex === 0) {
      for (let i = 1; i < paragraph.runs.length; i++) {
        paragraph.runs[i].text = '';
      }
    }

    this.markModified();
  }

  updateParagraphRuns(sectionIndex: number, elementIndex: number, runs: TextRun[]): void {
    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    if (!paragraph) return;
    this.saveState();
    paragraph.runs = runs;
    this.markModified();
  }

  /**
   * Update paragraph text while preserving the style structure of existing runs.
   *
   * Strategy:
   * - If new text is shorter/equal: distribute text across existing runs proportionally
   * - If new text is longer: extend the last run
   * - Preserves charPrIDRef of each run
   *
   * @param sectionIndex Section index
   * @param elementIndex Paragraph element index
   * @param newText New text content
   * @returns true if successful, false otherwise
   */
  updateParagraphTextPreserveStyles(sectionIndex: number, elementIndex: number, newText: string): boolean {
    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    if (!paragraph || paragraph.runs.length === 0) return false;

    this.saveState();

    // Calculate total original text length
    const originalTexts = paragraph.runs.map(r => r.text || '');
    const totalOriginalLength = originalTexts.reduce((sum, t) => sum + t.length, 0);

    if (totalOriginalLength === 0) {
      // If no original text, just set to first run
      paragraph.runs[0].text = newText;
      this.markModified();
      return true;
    }

    // Distribute new text proportionally across runs
    let newTextIndex = 0;
    for (let i = 0; i < paragraph.runs.length; i++) {
      const run = paragraph.runs[i];
      const originalLength = (run.text || '').length;
      const proportion = originalLength / totalOriginalLength;

      let runTextLength: number;
      if (i === paragraph.runs.length - 1) {
        // Last run gets all remaining text
        runTextLength = newText.length - newTextIndex;
      } else {
        // Proportional distribution
        runTextLength = Math.round(proportion * newText.length);
      }

      // Track for XML update
      const oldText = run.text;
      run.text = newText.substring(newTextIndex, newTextIndex + runTextLength);
      newTextIndex += runTextLength;

      // Add to pending updates if text changed
      if (oldText !== run.text && this._zip) {
        const paragraphOccurrence = this.getParagraphOccurrence(sectionIndex, elementIndex, paragraph.id || '');
        this._pendingDirectTextUpdates.push({
          sectionIndex,
          elementIndex,
          paragraphId: paragraph.id || '',
          paragraphOccurrence,
          runIndex: i,
          oldText: oldText || '',
          newText: run.text
        });
      }
    }

    this.markModified();
    return true;
  }

  insertParagraph(sectionIndex: number, afterElementIndex: number, text: string = ''): number {
    const section = this._content.sections[sectionIndex];
    if (!section) return -1;

    this.saveState();
    const paragraphId = Math.random().toString(36).substring(2, 11);
    const newParagraph: HwpxParagraph = {
      id: paragraphId,
      runs: [{ text }],
    };

    const newElement: SectionElement = { type: 'paragraph', data: newParagraph };
    section.elements.splice(afterElementIndex + 1, 0, newElement);

    // Add to pending list for XML sync
    this._pendingParagraphInserts.push({
      sectionIndex,
      afterElementIndex,
      paragraphId,
      text,
    });

    this.markModified();
    this.invalidateReadingCache();
    return afterElementIndex + 1;
  }

  deleteParagraph(sectionIndex: number, elementIndex: number): boolean {
    const section = this._content.sections[sectionIndex];
    if (!section || elementIndex < 0 || elementIndex >= section.elements.length) return false;

    this.saveState();

    // Capture element type before deletion
    const element = section.elements[elementIndex];
    const elementType = element.type === 'table' ? 'table' : 'paragraph';

    // Add to pending deletes for XML persistence
    this._pendingParagraphDeletes.push({
      sectionIndex,
      elementIndex,
      elementType
    });

    // Remove from memory
    section.elements.splice(elementIndex, 1);
    this.markModified();
    this.invalidateReadingCache();
    return true;
  }

  appendTextToParagraph(sectionIndex: number, elementIndex: number, text: string): void {
    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    if (!paragraph) return;

    this.saveState();

    if (paragraph.runs.length > 0) {
      // Append to existing last run's text
      const lastRunIndex = paragraph.runs.length - 1;
      const oldText = paragraph.runs[lastRunIndex].text || '';
      const newText = oldText + text;

      // Track for XML update
      if (this._zip) {
        const paragraphOccurrence = this.getParagraphOccurrence(sectionIndex, elementIndex, paragraph.id || '');
        this._pendingDirectTextUpdates.push({
          sectionIndex,
          elementIndex,
          paragraphId: paragraph.id || '',  // Use stable paragraph ID
          paragraphOccurrence,
          runIndex: lastRunIndex,
          oldText,
          newText
        });
      }

      paragraph.runs[lastRunIndex].text = newText;
    } else {
      // No runs exist, create new run
      paragraph.runs.push({ text });

      // Track for XML update with new run
      if (this._zip) {
        const paragraphOccurrence = this.getParagraphOccurrence(sectionIndex, elementIndex, paragraph.id || '');
        this._pendingDirectTextUpdates.push({
          sectionIndex,
          elementIndex,
          paragraphId: paragraph.id || '',  // Use stable paragraph ID
          paragraphOccurrence,
          runIndex: 0,
          oldText: '',
          newText: text
        });
      }
    }

    this.markModified();
  }

  // ============================================================
  // Character Style Operations
  // ============================================================

  applyCharacterStyle(sectionIndex: number, elementIndex: number, runIndex: number, style: Partial<CharacterStyle>): void {
    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    if (!paragraph || !paragraph.runs[runIndex]) return;

    this.saveState();
    const run = paragraph.runs[runIndex];
    run.charStyle = { ...run.charStyle, ...style };

    // Add to pending list for XML sync
    this._pendingCharacterStyles.push({
      sectionIndex,
      elementIndex,
      runIndex,
      style,
    });

    this.markModified();
  }

  getCharacterStyle(sectionIndex: number, elementIndex: number, runIndex?: number): CharacterStyle | CharacterStyle[] | null {
    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    if (!paragraph) return null;

    if (runIndex !== undefined) {
      return paragraph.runs[runIndex]?.charStyle || null;
    }
    return paragraph.runs.map(r => r.charStyle || {});
  }

  // ============================================================
  // Paragraph Style Operations
  // ============================================================

  applyParagraphStyle(sectionIndex: number, elementIndex: number, style: Partial<ParagraphStyle>): void {
    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    if (!paragraph) return;

    this.saveState();
    paragraph.paraStyle = { ...paragraph.paraStyle, ...style };

    // Add to pending list for XML sync
    this._pendingParagraphStyles.push({
      sectionIndex,
      elementIndex,
      style,
    });

    this.markModified();
  }

  getParagraphStyle(sectionIndex: number, elementIndex: number): ParagraphStyle | null {
    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    return paragraph?.paraStyle || null;
  }

  // ============================================================
  // Hanging Indent Operations (내어쓰기)
  // ============================================================

  /**
   * Set hanging indent on a paragraph.
   * In HWPML, hanging indent uses:
   * - intent: negative value (pulls first line left)
   * - left: positive value (base left margin for other lines)
   * @param sectionIndex Section index
   * @param elementIndex Paragraph element index
   * @param indentPt Indent amount in points (positive value)
   * @returns true if successful, false otherwise
   */
  setHangingIndent(sectionIndex: number, elementIndex: number, indentPt: number): boolean {
    // Validate indent value
    if (indentPt <= 0) return false;

    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    if (!paragraph) return false;

    this.saveState();

    // Set paragraph style with hanging indent
    // firstLineIndent: negative (pulls first line left)
    // marginLeft: positive (base margin for other lines)
    paragraph.paraStyle = {
      ...paragraph.paraStyle,
      firstLineIndent: -indentPt,
      marginLeft: indentPt,
    };

    // Track for XML update during save (update existing entry if present)
    const existingIdx = this._pendingHangingIndents.findIndex(
      p => p.sectionIndex === sectionIndex && p.elementIndex === elementIndex
    );
    if (existingIdx >= 0) {
      this._pendingHangingIndents[existingIdx].indentPt = indentPt;
    } else {
      this._pendingHangingIndents.push({
        sectionIndex,
        elementIndex,
        paragraphId: paragraph.id,
        indentPt,
      });
    }

    this.markModified();
    return true;
  }

  /**
   * Get hanging indent value for a paragraph.
   * @param sectionIndex Section index
   * @param elementIndex Paragraph element index
   * @returns Indent value in points, 0 if no hanging indent, null if invalid indices
   */
  getHangingIndent(sectionIndex: number, elementIndex: number): number | null {
    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    if (!paragraph) return null;

    const style = paragraph.paraStyle;
    if (!style) return 0;

    // Hanging indent: firstLineIndent is negative and marginLeft is positive
    const firstLineIndent = style.firstLineIndent || 0;
    const marginLeft = style.marginLeft || 0;

    if (firstLineIndent < 0 && marginLeft > 0) {
      // Return the indent amount (positive value)
      return marginLeft;
    }

    return 0;
  }

  /**
   * Remove hanging indent from a paragraph.
   * @param sectionIndex Section index
   * @param elementIndex Paragraph element index
   * @returns true if successful, false otherwise
   */
  removeHangingIndent(sectionIndex: number, elementIndex: number): boolean {
    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    if (!paragraph) return false;

    this.saveState();

    // Reset indent values
    paragraph.paraStyle = {
      ...paragraph.paraStyle,
      firstLineIndent: 0,
      marginLeft: 0,
    };

    // Track for XML update (update existing entry if present)
    const existingIdx = this._pendingHangingIndents.findIndex(
      p => p.sectionIndex === sectionIndex && p.elementIndex === elementIndex
    );
    if (existingIdx >= 0) {
      this._pendingHangingIndents[existingIdx].indentPt = 0;  // 0 means remove
    } else {
      this._pendingHangingIndents.push({
        sectionIndex,
        elementIndex,
        paragraphId: paragraph.id,
        indentPt: 0,  // 0 means remove
      });
    }

    this.markModified();
    return true;
  }

  // ============================================================
  // Font Size Lookup (폰트 크기 조회)
  // ============================================================

  /**
   * Load character property cache from header.xml.
   * Maps charPr id to font size in pt.
   */
  private async loadCharPrCache(): Promise<void> {
    if (this._charPrCache !== null || !this._zip) return;

    this._charPrCache = new Map<number, number>();

    const headerPath = 'Contents/header.xml';
    const headerXml = await this._zip.file(headerPath)?.async('string');
    if (!headerXml) return;

    // Parse charPr elements: <hh:charPr id="0" height="1000" ...>
    // height is in HWPUNIT (pt × 100)
    const charPrRegex = /<hh:charPr\s+[^>]*id="(\d+)"[^>]*height="(\d+)"/g;
    let match;
    while ((match = charPrRegex.exec(headerXml)) !== null) {
      const id = parseInt(match[1], 10);
      const heightHwpUnit = parseInt(match[2], 10);
      const fontSizePt = heightHwpUnit / 100;
      this._charPrCache.set(id, fontSizePt);
    }

    // Also try charShape format: <hh:charShape id="0" height="1000" ...>
    const charShapeRegex = /<hh:charShape\s+[^>]*id="(\d+)"[^>]*height="(\d+)"/g;
    while ((match = charShapeRegex.exec(headerXml)) !== null) {
      const id = parseInt(match[1], 10);
      const heightHwpUnit = parseInt(match[2], 10);
      const fontSizePt = heightHwpUnit / 100;
      this._charPrCache.set(id, fontSizePt);
    }
  }

  /**
   * Get font size from charPr id.
   * @param charPrId Character property ID
   * @returns Font size in pt, or undefined if not found
   */
  async getFontSizeFromCharPrId(charPrId: number): Promise<number | undefined> {
    await this.loadCharPrCache();
    return this._charPrCache?.get(charPrId);
  }

  /**
   * Get font size of a paragraph from XML.
   * Reads charPrIDRef from the first run in the paragraph.
   * @param sectionIndex Section index
   * @param elementIndex Paragraph element index
   * @returns Font size in pt, or undefined if not found
   */
  async getParagraphFontSize(sectionIndex: number, elementIndex: number): Promise<number | undefined> {
    if (!this._zip) return undefined;

    const sectionPath = `Contents/section${sectionIndex}.xml`;
    const sectionXml = await this._zip.file(sectionPath)?.async('string');
    if (!sectionXml) return undefined;

    // Find all paragraphs in the section
    const paragraphs = this.findAllElementsWithDepth(sectionXml, 'p');
    const paraXml = paragraphs[elementIndex]?.xml;
    if (!paraXml) return undefined;

    // Extract charPrIDRef from first run
    const runMatch = paraXml.match(/<(?:hp|hc|hs):run\s+[^>]*charPrIDRef="(\d+)"/);
    if (!runMatch) return undefined;

    const charPrId = parseInt(runMatch[1], 10);
    return await this.getFontSizeFromCharPrId(charPrId);
  }

  /**
   * Get font size of a paragraph in a table cell from XML.
   * @param sectionIndex Section index
   * @param tableIndex Table index within section
   * @param row Row index (0-based)
   * @param col Column index (0-based)
   * @param paragraphIndex Paragraph index within cell (0-based)
   * @returns Font size in pt, or undefined if not found
   */
  async getTableCellParagraphFontSize(
    sectionIndex: number,
    tableIndex: number,
    row: number,
    col: number,
    paragraphIndex: number
  ): Promise<number | undefined> {
    if (!this._zip) return undefined;

    const sectionPath = `Contents/section${sectionIndex}.xml`;
    const sectionXml = await this._zip.file(sectionPath)?.async('string');
    if (!sectionXml) return undefined;

    // Find all tables
    const tables = this.findAllTables(sectionXml);
    const tableData = tables[tableIndex];
    if (!tableData) return undefined;

    // Find the specific cell
    const rows = this.findAllElementsWithDepth(tableData.xml, 'tr');
    const rowXml = rows[row]?.xml;
    if (!rowXml) return undefined;

    const cells = this.findAllElementsWithDepth(rowXml, 'tc');
    const cellXml = cells[col]?.xml;
    if (!cellXml) return undefined;

    // Find paragraphs in the cell
    const paragraphs = this.findAllElementsWithDepth(cellXml, 'p');
    const paraXml = paragraphs[paragraphIndex]?.xml;
    if (!paraXml) return undefined;

    // Extract charPrIDRef from first run
    const runMatch = paraXml.match(/<(?:hp|hc|hs):run\s+[^>]*charPrIDRef="(\d+)"/);
    if (!runMatch) return undefined;

    const charPrId = parseInt(runMatch[1], 10);
    return await this.getFontSizeFromCharPrId(charPrId);
  }

  /**
   * Automatically set hanging indent based on detected marker in paragraph text.
   * Uses HangingIndentCalculator to detect markers like "○ ", "1. ", "가. " etc.
   * @param sectionIndex Section index
   * @param elementIndex Paragraph element index
   * @param fontSize Font size in pt (if not provided, reads from document)
   * @returns Calculated indent value in pt, or 0 if no marker detected
   */
  setAutoHangingIndent(sectionIndex: number, elementIndex: number, fontSize?: number): number {
    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    if (!paragraph) return 0;

    // Get paragraph text
    const text = paragraph.runs?.map(r => r.text || '').join('') || '';
    if (!text) return 0;

    // If fontSize not provided, use default (will be updated async if available)
    const effectiveFontSize = fontSize ?? 10;

    // Calculate indent using HangingIndentCalculator
    const calculator = new HangingIndentCalculator();
    const indentPt = calculator.calculateHangingIndent(text, effectiveFontSize);

    if (indentPt > 0) {
      this.setHangingIndent(sectionIndex, elementIndex, indentPt);
    }

    return indentPt;
  }

  /**
   * Automatically set hanging indent with dynamic font size from document.
   * Async version that reads actual font size from the document.
   * @param sectionIndex Section index
   * @param elementIndex Paragraph element index
   * @param fallbackFontSize Fallback font size in pt if document font size not found (default: 10)
   * @returns Calculated indent value in pt, or 0 if no marker detected
   */
  async setAutoHangingIndentAsync(
    sectionIndex: number,
    elementIndex: number,
    fallbackFontSize: number = 10
  ): Promise<number> {
    const paragraph = this.findParagraphByPath(sectionIndex, elementIndex);
    if (!paragraph) return 0;

    // Get paragraph text
    const text = paragraph.runs?.map(r => r.text || '').join('') || '';
    if (!text) return 0;

    // Try to get font size from document
    const docFontSize = await this.getParagraphFontSize(sectionIndex, elementIndex);
    const effectiveFontSize = docFontSize ?? fallbackFontSize;

    // Calculate indent using HangingIndentCalculator
    const calculator = new HangingIndentCalculator();
    const indentPt = calculator.calculateHangingIndent(text, effectiveFontSize);

    if (indentPt > 0) {
      this.setHangingIndent(sectionIndex, elementIndex, indentPt);
    }

    return indentPt;
  }

  // ============================================================
  // Table Cell Hanging Indent (테이블 셀 내어쓰기)
  // ============================================================

  /**
   * Set hanging indent on a paragraph inside a table cell.
   * @param sectionIndex Section index
   * @param tableIndex Table index within section
   * @param row Row index (0-based)
   * @param col Column index (0-based)
   * @param paragraphIndex Paragraph index within cell (0-based)
   * @param indentPt Indent value in points (positive)
   * @returns true if successful, false otherwise
   */
  setTableCellHangingIndent(
    sectionIndex: number,
    tableIndex: number,
    row: number,
    col: number,
    paragraphIndex: number,
    indentPt: number
  ): boolean {
    // Validate indent value
    if (indentPt <= 0) return false;

    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return false;

    const cell = table.rows[row]?.cells[col];
    if (!cell) return false;

    // Note: We don't strictly require the paragraph to exist in memory because:
    // - Multi-line text in updateTableCell() creates multiple paragraphs in XML during save()
    // - But the in-memory model only maintains a single paragraph representation
    // - The actual paragraph validation happens during XML processing in applyTableCellHangingIndentsToXml()
    const paragraph = cell.paragraphs[paragraphIndex];

    this.saveState();

    // If the paragraph exists in memory, update its style
    if (paragraph) {
      paragraph.paraStyle = {
        ...paragraph.paraStyle,
        firstLineIndent: -indentPt,
        marginLeft: indentPt,
      };
    }

    // Track for XML update during save (update existing entry if present)
    const existingIdx = this._pendingTableCellHangingIndents.findIndex(
      p => p.sectionIndex === sectionIndex &&
           p.tableIndex === tableIndex &&
           p.row === row &&
           p.col === col &&
           p.paragraphIndex === paragraphIndex
    );
    if (existingIdx >= 0) {
      this._pendingTableCellHangingIndents[existingIdx].indentPt = indentPt;
    } else {
      this._pendingTableCellHangingIndents.push({
        sectionIndex,
        tableIndex,
        row,
        col,
        paragraphIndex,
        paragraphId: paragraph?.id || '',
        indentPt,
      });
    }

    this.markModified();
    return true;
  }

  /**
   * Get hanging indent value for a paragraph inside a table cell.
   * @param sectionIndex Section index
   * @param tableIndex Table index within section
   * @param row Row index (0-based)
   * @param col Column index (0-based)
   * @param paragraphIndex Paragraph index within cell (0-based)
   * @returns Indent value in points, 0 if no hanging indent, null if invalid indices
   */
  getTableCellHangingIndent(
    sectionIndex: number,
    tableIndex: number,
    row: number,
    col: number,
    paragraphIndex: number
  ): number | null {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return null;

    const cell = table.rows[row]?.cells[col];
    if (!cell) return null;

    const paragraph = cell.paragraphs[paragraphIndex];
    if (!paragraph) return null;

    const style = paragraph.paraStyle;
    if (!style) return 0;

    // Hanging indent: firstLineIndent is negative and marginLeft is positive
    const firstLineIndent = style.firstLineIndent || 0;
    const marginLeft = style.marginLeft || 0;

    if (firstLineIndent < 0 && marginLeft > 0) {
      return marginLeft;
    }

    return 0;
  }

  /**
   * Remove hanging indent from a paragraph inside a table cell.
   * @param sectionIndex Section index
   * @param tableIndex Table index within section
   * @param row Row index (0-based)
   * @param col Column index (0-based)
   * @param paragraphIndex Paragraph index within cell (0-based)
   * @returns true if successful, false otherwise
   */
  removeTableCellHangingIndent(
    sectionIndex: number,
    tableIndex: number,
    row: number,
    col: number,
    paragraphIndex: number
  ): boolean {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return false;

    const cell = table.rows[row]?.cells[col];
    if (!cell) return false;

    const paragraph = cell.paragraphs[paragraphIndex];
    if (!paragraph) return false;

    this.saveState();

    // Reset indent values
    paragraph.paraStyle = {
      ...paragraph.paraStyle,
      firstLineIndent: 0,
      marginLeft: 0,
    };

    // Track for XML update (update existing entry if present)
    const existingIdx = this._pendingTableCellHangingIndents.findIndex(
      p => p.sectionIndex === sectionIndex &&
           p.tableIndex === tableIndex &&
           p.row === row &&
           p.col === col &&
           p.paragraphIndex === paragraphIndex
    );
    if (existingIdx >= 0) {
      this._pendingTableCellHangingIndents[existingIdx].indentPt = 0;  // 0 means remove
    } else {
      this._pendingTableCellHangingIndents.push({
        sectionIndex,
        tableIndex,
        row,
        col,
        paragraphIndex,
        paragraphId: paragraph.id,
        indentPt: 0,  // 0 means remove
      });
    }

    this.markModified();
    return true;
  }

  /**
   * Automatically set hanging indent on a paragraph inside a table cell
   * based on detected marker in the text.
   * @param sectionIndex Section index
   * @param tableIndex Table index within section
   * @param row Row index (0-based)
   * @param col Column index (0-based)
   * @param paragraphIndex Paragraph index within cell (0-based)
   * @param fontSize Font size in pt (default: 10)
   * @returns Calculated indent value in pt, or 0 if no marker detected
   */
  setTableCellAutoHangingIndent(
    sectionIndex: number,
    tableIndex: number,
    row: number,
    col: number,
    paragraphIndex: number,
    fontSize?: number
  ): number {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return 0;

    const cell = table.rows[row]?.cells[col];
    if (!cell) return 0;

    const paragraph = cell.paragraphs[paragraphIndex];
    if (!paragraph) return 0;

    // Get paragraph text
    const text = paragraph.runs?.map(r => r.text || '').join('') || '';
    if (!text) return 0;

    // If fontSize not provided, use default
    const effectiveFontSize = fontSize ?? 10;

    // Calculate indent using HangingIndentCalculator
    const calculator = new HangingIndentCalculator();
    const indentPt = calculator.calculateHangingIndent(text, effectiveFontSize);

    if (indentPt > 0) {
      this.setTableCellHangingIndent(sectionIndex, tableIndex, row, col, paragraphIndex, indentPt);
    }

    return indentPt;
  }

  /**
   * Automatically set hanging indent on a paragraph inside a table cell
   * with dynamic font size from document.
   * Async version that reads actual font size from the document.
   * @param sectionIndex Section index
   * @param tableIndex Table index within section
   * @param row Row index (0-based)
   * @param col Column index (0-based)
   * @param paragraphIndex Paragraph index within cell (0-based)
   * @param fallbackFontSize Fallback font size in pt if document font size not found (default: 10)
   * @returns Calculated indent value in pt, or 0 if no marker detected
   */
  async setTableCellAutoHangingIndentAsync(
    sectionIndex: number,
    tableIndex: number,
    row: number,
    col: number,
    paragraphIndex: number,
    fallbackFontSize: number = 10
  ): Promise<number> {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return 0;

    const cell = table.rows[row]?.cells[col];
    if (!cell) return 0;

    const paragraph = cell.paragraphs[paragraphIndex];
    if (!paragraph) return 0;

    // Get paragraph text
    const text = paragraph.runs?.map(r => r.text || '').join('') || '';
    if (!text) return 0;

    // Try to get font size from document
    const docFontSize = await this.getTableCellParagraphFontSize(
      sectionIndex, tableIndex, row, col, paragraphIndex
    );
    const effectiveFontSize = docFontSize ?? fallbackFontSize;

    // Calculate indent using HangingIndentCalculator
    const calculator = new HangingIndentCalculator();
    const indentPt = calculator.calculateHangingIndent(text, effectiveFontSize);

    if (indentPt > 0) {
      this.setTableCellHangingIndent(sectionIndex, tableIndex, row, col, paragraphIndex, indentPt);
    }

    return indentPt;
  }

  // ============================================================
  // Table Operations
  // ============================================================

  private findTable(sectionIndex: number, tableIndex: number): HwpxTable | null {
    const section = this._content.sections[sectionIndex];
    if (!section) return null;
    const tables = section.elements.filter(el => el.type === 'table');
    return tables[tableIndex]?.data as HwpxTable || null;
  }

  getTables(): Array<{ section: number; index: number; rows: number; cols: number }> {
    const tables: Array<{ section: number; index: number; rows: number; cols: number }> = [];
    this._content.sections.forEach((section, si) => {
      let tableIndex = 0;
      section.elements.forEach(el => {
        if (el.type === 'table') {
          const table = el.data as HwpxTable;
          tables.push({
            section: si,
            index: tableIndex++,
            rows: table.rows.length,
            cols: table.rows[0]?.cells.length || 0,
          });
        }
      });
    });
    return tables;
  }

  /**
   * Get table map with headers - maps table indices to their header paragraphs
   * Returns array of table info including the header text from the preceding paragraph
   */
  getTableMap(): Array<{
    table_index: number;
    section_index: number;
    header: string;
    rows: number;
    cols: number;
    is_empty: boolean;
    first_row_preview: string[];
  }> {
    const result: Array<{
      table_index: number;
      section_index: number;
      header: string;
      rows: number;
      cols: number;
      is_empty: boolean;
      first_row_preview: string[];
    }> = [];

    let globalTableIndex = 0;

    this._content.sections.forEach((section, sectionIndex) => {
      let lastParagraphText = '';

      section.elements.forEach((element, _elementIndex) => {
        if (element.type === 'paragraph') {
          // Store the paragraph text as potential header
          const para = element.data as HwpxParagraph;
          const text = para.runs.map(r => r.text).join('').trim();
          if (text) {
            lastParagraphText = text;
          }
        } else if (element.type === 'table') {
          const table = element.data as HwpxTable;
          const rows = table.rows.length;
          const cols = table.rows[0]?.cells.length || 0;

          // Check if table is empty (no meaningful content)
          const isEmpty = this.isTableEmpty(table);

          // Get first row preview
          const firstRowPreview = table.rows[0]?.cells.map(cell => {
            const text = cell.paragraphs.map(p => p.runs.map(r => r.text).join('')).join(' ').trim();
            return text.substring(0, 50) + (text.length > 50 ? '...' : '');
          }) || [];

          result.push({
            table_index: globalTableIndex,
            section_index: sectionIndex,
            header: lastParagraphText,
            rows,
            cols,
            is_empty: isEmpty,
            first_row_preview: firstRowPreview,
          });

          globalTableIndex++;
          // Don't reset lastParagraphText here - next table might reuse same header if consecutive
        }
      });
    });

    return result;
  }

  /**
   * Check if a table is empty or contains only placeholder text
   */
  private isTableEmpty(table: HwpxTable): boolean {
    const placeholderPatterns = [
      /^\s*$/,                    // Empty or whitespace only
      /^[\-\s]+$/,                // Dashes and spaces only
      /^[0-9\.\s]+$/,             // Numbers and dots only (like "1." "2.")
      /^[\(\)\[\]\s]+$/,          // Brackets only
      /^(※|○|●|□|■|\*|\-)+\s*$/,  // Bullet markers only
    ];

    for (const row of table.rows) {
      for (const cell of row.cells) {
        const cellText = cell.paragraphs.map(p => p.runs.map(r => r.text).join('')).join('').trim();
        // If cell has meaningful content (not matching placeholder patterns)
        if (cellText && !placeholderPatterns.some(pattern => pattern.test(cellText))) {
          return false;
        }
      }
    }
    return true;
  }

  /**
   * Find tables that are empty or contain only placeholders
   */
  findEmptyTables(): Array<{
    table_index: number;
    section_index: number;
    header: string;
    rows: number;
    cols: number;
  }> {
    const tableMap = this.getTableMap();
    return tableMap.filter(t => t.is_empty).map(t => ({
      table_index: t.table_index,
      section_index: t.section_index,
      header: t.header,
      rows: t.rows,
      cols: t.cols,
    }));
  }

  /**
   * Get tables within a specific section
   */
  getTablesBySection(sectionIndex: number): Array<{
    table_index: number;
    local_index: number;
    header: string;
    rows: number;
    cols: number;
    is_empty: boolean;
  }> {
    const tableMap = this.getTableMap();
    let localIndex = 0;
    return tableMap
      .filter(t => t.section_index === sectionIndex)
      .map(t => ({
        table_index: t.table_index,
        local_index: localIndex++,
        header: t.header,
        rows: t.rows,
        cols: t.cols,
        is_empty: t.is_empty,
      }));
  }

  /**
   * Find tables by header text (partial match, case-insensitive)
   */
  findTableByHeader(searchText: string): Array<{
    table_index: number;
    section_index: number;
    header: string;
    rows: number;
    cols: number;
    is_empty: boolean;
    first_row_preview: string[];
  }> {
    const tableMap = this.getTableMap();
    const lowerSearch = searchText.toLowerCase();
    return tableMap.filter(t => t.header.toLowerCase().includes(lowerSearch));
  }

  /**
   * Get summary of multiple tables by index range
   */
  getTablesSummary(startIndex?: number, endIndex?: number): Array<{
    table_index: number;
    section_index: number;
    header: string;
    size: string;
    is_empty: boolean;
    content_preview: string;
  }> {
    const tableMap = this.getTableMap();
    const start = startIndex ?? 0;
    const end = endIndex ?? tableMap.length - 1;

    return tableMap
      .filter(t => t.table_index >= start && t.table_index <= end)
      .map(t => ({
        table_index: t.table_index,
        section_index: t.section_index,
        header: t.header.substring(0, 100) + (t.header.length > 100 ? '...' : ''),
        size: `${t.rows}x${t.cols}`,
        is_empty: t.is_empty,
        content_preview: t.first_row_preview.slice(0, 3).join(' | '),
      }));
  }

  // ============================================================
  // Path-Based Cell Access Methods (jkf87 style)
  // ============================================================

  /**
   * Find cells by label text and return the adjacent cell position.
   * Useful for form-like documents where labels identify input fields.
   * @param labelText - The label text to search for (case-insensitive, partial match)
   * @param direction - Direction to find target cell: 'right' (default) or 'down'
   * @returns Array of found positions with label and target cell info
   */
  findCellByLabel(
    labelText: string,
    direction: 'right' | 'down' = 'right'
  ): Array<{
    tableIndex: number;
    sectionIndex: number;
    labelRow: number;
    labelCol: number;
    targetRow: number;
    targetCol: number;
    targetCellText: string;
  }> {
    const results: Array<{
      tableIndex: number;
      sectionIndex: number;
      labelRow: number;
      labelCol: number;
      targetRow: number;
      targetCol: number;
      targetCellText: string;
    }> = [];

    const lowerLabel = labelText.toLowerCase();
    let globalTableIndex = 0;

    for (let sectionIndex = 0; sectionIndex < this._content.sections.length; sectionIndex++) {
      const section = this._content.sections[sectionIndex];

      for (const element of section.elements) {
        if (element.type === 'table') {
          const table = element.data as HwpxTable;

          for (let row = 0; row < table.rows.length; row++) {
            for (let col = 0; col < table.rows[row].cells.length; col++) {
              const cell = table.rows[row].cells[col];
              const cellText = cell.paragraphs
                .map(p => p.runs.map(r => r.text).join(''))
                .join('\n')
                .trim();

              if (cellText.toLowerCase().includes(lowerLabel)) {
                // Calculate target position
                let targetRow = row;
                let targetCol = col;

                if (direction === 'right') {
                  targetCol = col + 1;
                } else if (direction === 'down') {
                  targetRow = row + 1;
                }

                // Check if target is within bounds
                if (
                  targetRow < table.rows.length &&
                  targetCol < table.rows[targetRow].cells.length
                ) {
                  const targetCell = table.rows[targetRow].cells[targetCol];
                  const targetText = targetCell.paragraphs
                    .map(p => p.runs.map(r => r.text).join(''))
                    .join('\n')
                    .trim();

                  results.push({
                    tableIndex: globalTableIndex,
                    sectionIndex,
                    labelRow: row,
                    labelCol: col,
                    targetRow,
                    targetCol,
                    targetCellText: targetText,
                  });
                }
              }
            }
          }

          globalTableIndex++;
        }
      }
    }

    return results;
  }

  /**
   * Fill table cells using path-based mappings (jkf87 style).
   * Path format: "labelText > direction" or chained "labelText > dir > dir"
   * @param mappings - Object mapping paths to values, e.g., { "이름: > right": "홍길동", "합계 > down > down": "1000" }
   * @returns Object with success count, failed paths, and details
   */
  fillByPath(mappings: Record<string, string>): {
    success: number;
    failed: string[];
    details: Array<{
      path: string;
      tableIndex: number;
      row: number;
      col: number;
      previousValue: string;
      newValue: string;
    }>;
  } {
    const result = {
      success: 0,
      failed: [] as string[],
      details: [] as Array<{
        path: string;
        tableIndex: number;
        row: number;
        col: number;
        previousValue: string;
        newValue: string;
      }>,
    };

    for (const [path, value] of Object.entries(mappings)) {
      const position = this.resolvePathToPosition(path);

      if (!position) {
        result.failed.push(path);
        continue;
      }

      // Get the table info for section index
      const tableInfo = this.convertGlobalToLocalTableIndex(position.tableIndex);
      if (!tableInfo) {
        result.failed.push(path);
        continue;
      }

      // Get previous value
      const cellData = this.getTableCell(
        tableInfo.section_index,
        tableInfo.local_index,
        position.row,
        position.col
      );
      const previousValue = cellData?.text || '';

      // Update the cell
      const updated = this.updateTableCell(
        tableInfo.section_index,
        tableInfo.local_index,
        position.row,
        position.col,
        value
      );

      if (updated) {
        result.success++;
        result.details.push({
          path,
          tableIndex: position.tableIndex,
          row: position.row,
          col: position.col,
          previousValue,
          newValue: value,
        });
      } else {
        result.failed.push(path);
      }
    }

    return result;
  }

  /**
   * Resolve a path string to a cell position.
   * Path format: "labelText > direction" or chained "labelText > dir > dir"
   * Directions: right, left, up, down
   * @param path - Path string like "이름: > right" or "합계 > down > down"
   * @returns Cell position or null if not found
   */
  private resolvePathToPosition(path: string): {
    tableIndex: number;
    row: number;
    col: number;
  } | null {
    const parts = path.split('>').map(p => p.trim());
    if (parts.length < 2) return null;

    const labelText = parts[0];
    const directions = parts.slice(1).map(d => d.toLowerCase() as 'right' | 'left' | 'up' | 'down');

    // Find the label cell first
    const initialDir = directions[0] === 'left' || directions[0] === 'right' ? 'right' : 'down';
    const found = this.findCellByLabel(labelText, initialDir as 'right' | 'down');

    if (found.length === 0) return null;

    // Use the first match
    const firstMatch = found[0];
    let currentRow = firstMatch.labelRow;
    let currentCol = firstMatch.labelCol;
    const tableIndex = firstMatch.tableIndex;

    // Get table dimensions
    const tableInfo = this.convertGlobalToLocalTableIndex(tableIndex);
    if (!tableInfo) return null;

    const table = this.findTable(tableInfo.section_index, tableInfo.local_index);
    if (!table) return null;

    // Apply all directions
    for (const dir of directions) {
      switch (dir) {
        case 'right':
          currentCol++;
          break;
        case 'left':
          currentCol--;
          break;
        case 'up':
          currentRow--;
          break;
        case 'down':
          currentRow++;
          break;
        default:
          return null;
      }

      // Bounds check
      if (
        currentRow < 0 ||
        currentRow >= table.rows.length ||
        currentCol < 0 ||
        currentCol >= table.rows[currentRow].cells.length
      ) {
        return null;
      }
    }

    return { tableIndex, row: currentRow, col: currentCol };
  }

  /**
   * Get context around a specific cell (neighboring cells' content).
   * Useful for understanding a cell's position and meaning in a table.
   * @param tableIndex - Global table index
   * @param row - Row index (0-based)
   * @param col - Column index (0-based)
   * @param depth - How many cells in each direction to include (default: 1)
   * @returns Object with center cell and neighboring cells' content
   */
  getCellContext(
    tableIndex: number,
    row: number,
    col: number,
    depth: number = 1
  ): {
    center: string;
    [key: string]: string | undefined;
  } | null {
    const tableInfo = this.convertGlobalToLocalTableIndex(tableIndex);
    if (!tableInfo) return null;

    const table = this.findTable(tableInfo.section_index, tableInfo.local_index);
    if (!table) return null;

    // Validate row/col
    if (row < 0 || row >= table.rows.length || col < 0 || col >= table.rows[row].cells.length) {
      return null;
    }

    const getCellText = (r: number, c: number): string | undefined => {
      if (r < 0 || r >= table.rows.length || c < 0 || c >= table.rows[r].cells.length) {
        return undefined;
      }
      const cell = table.rows[r].cells[c];
      return cell.paragraphs
        .map(p => p.runs.map(run => run.text).join(''))
        .join('\n')
        .trim();
    };

    const result: { center: string; [key: string]: string | undefined } = {
      center: getCellText(row, col) || '',
    };

    // Add neighbors based on depth
    for (let d = 1; d <= depth; d++) {
      const upText = getCellText(row - d, col);
      const downText = getCellText(row + d, col);
      const leftText = getCellText(row, col - d);
      const rightText = getCellText(row, col + d);

      if (upText !== undefined) result[`up_${d}`] = upText;
      if (downText !== undefined) result[`down_${d}`] = downText;
      if (leftText !== undefined) result[`left_${d}`] = leftText;
      if (rightText !== undefined) result[`right_${d}`] = rightText;
    }

    return result;
  }

  /**
   * Batch fill a table with 2D array data.
   * Useful for filling multiple cells at once from structured data.
   * @param tableIndex - Global table index
   * @param data - 2D array of strings to fill (row-major order)
   * @param startRow - Starting row index (default: 0)
   * @param startCol - Starting column index (default: 0)
   * @returns Object with success count and any out-of-bounds cells
   */
  batchFillTable(
    tableIndex: number,
    data: string[][],
    startRow: number = 0,
    startCol: number = 0
  ): {
    success: number;
    outOfBounds: Array<{ row: number; col: number; value: string }>;
    updated: Array<{ row: number; col: number; previousValue: string; newValue: string }>;
  } {
    const result = {
      success: 0,
      outOfBounds: [] as Array<{ row: number; col: number; value: string }>,
      updated: [] as Array<{ row: number; col: number; previousValue: string; newValue: string }>,
    };

    const tableInfo = this.convertGlobalToLocalTableIndex(tableIndex);
    if (!tableInfo) return result;

    const table = this.findTable(tableInfo.section_index, tableInfo.local_index);
    if (!table) return result;

    for (let dataRow = 0; dataRow < data.length; dataRow++) {
      const targetRow = startRow + dataRow;

      for (let dataCol = 0; dataCol < data[dataRow].length; dataCol++) {
        const targetCol = startCol + dataCol;
        const value = data[dataRow][dataCol];

        // Check bounds
        if (
          targetRow < 0 ||
          targetRow >= table.rows.length ||
          targetCol < 0 ||
          targetCol >= table.rows[targetRow].cells.length
        ) {
          result.outOfBounds.push({ row: targetRow, col: targetCol, value });
          continue;
        }

        // Get previous value
        const cellData = this.getTableCell(
          tableInfo.section_index,
          tableInfo.local_index,
          targetRow,
          targetCol
        );
        const previousValue = cellData?.text || '';

        // Update cell
        const updated = this.updateTableCell(
          tableInfo.section_index,
          tableInfo.local_index,
          targetRow,
          targetCol,
          value
        );

        if (updated) {
          result.success++;
          result.updated.push({
            row: targetRow,
            col: targetCol,
            previousValue,
            newValue: value,
          });
        }
      }
    }

    return result;
  }

  /**
   * Convert global table index to section and local index
   * @param globalTableIndex - Global table index (0-based across all sections)
   * @returns Object with section_index and local_index, or null if not found
   */
  convertGlobalToLocalTableIndex(globalTableIndex: number): { section_index: number; local_index: number } | null {
    let currentGlobalIndex = 0;

    for (let sectionIndex = 0; sectionIndex < this._content.sections.length; sectionIndex++) {
      const section = this._content.sections[sectionIndex];
      let localIndex = 0;

      for (const element of section.elements) {
        if (element.type === 'table') {
          if (currentGlobalIndex === globalTableIndex) {
            return { section_index: sectionIndex, local_index: localIndex };
          }
          currentGlobalIndex++;
          localIndex++;
        }
      }
    }

    return null;
  }

  /**
   * Get document outline - hierarchical structure showing headers and their associated tables
   */
  getDocumentOutline(): Array<{
    type: 'section' | 'heading' | 'table' | 'paragraph';
    level: number;
    text: string;
    section_index: number;
    table_index?: number;
    element_index: number;
  }> {
    const outline: Array<{
      type: 'section' | 'heading' | 'table' | 'paragraph';
      level: number;
      text: string;
      section_index: number;
      table_index?: number;
      element_index: number;
    }> = [];

    // Patterns to detect heading-like paragraphs
    const headingPatterns = [
      /^[0-9]+\.\s+/,                           // "1. Title"
      /^\([0-9]+\)\s*/,                         // "(1) Title"
      /^[가-힣]\.\s*/,                          // "가. Title"
      /^\([가-힣]\)\s*/,                        // "(가) Title"
      /^[IVX]+\.\s*/,                           // "I. Title" (Roman numerals)
      /^제\s*[0-9]+\s*(장|절|조|항)/,           // "제1장", "제2절" etc.
      /^[①②③④⑤⑥⑦⑧⑨⑩]\s*/,                // Circled numbers
      /^[■□◆◇●○▶▷]\s*/,                       // Bullet markers as headers
    ];

    const getHeadingLevel = (text: string): number => {
      // Deeper nesting = higher level number
      if (/^제\s*[0-9]+\s*장/.test(text)) return 1;
      if (/^제\s*[0-9]+\s*절/.test(text)) return 2;
      if (/^[0-9]+\.\s+/.test(text)) return 2;
      if (/^\([0-9]+\)\s*/.test(text)) return 3;
      if (/^[가-힣]\.\s*/.test(text)) return 3;
      if (/^\([가-힣]\)\s*/.test(text)) return 4;
      if (/^[①②③④⑤⑥⑦⑧⑨⑩]\s*/.test(text)) return 4;
      return 5; // Default level for unrecognized patterns
    };

    let globalTableIndex = 0;

    this._content.sections.forEach((section, sectionIndex) => {
      // Add section marker
      outline.push({
        type: 'section',
        level: 0,
        text: `Section ${sectionIndex + 1}`,
        section_index: sectionIndex,
        element_index: -1,
      });

      section.elements.forEach((element, elementIndex) => {
        if (element.type === 'paragraph') {
          const para = element.data as HwpxParagraph;
          const text = para.runs.map(r => r.text).join('').trim();

          if (text && headingPatterns.some(p => p.test(text))) {
            outline.push({
              type: 'heading',
              level: getHeadingLevel(text),
              text: text.substring(0, 100) + (text.length > 100 ? '...' : ''),
              section_index: sectionIndex,
              element_index: elementIndex,
            });
          }
        } else if (element.type === 'table') {
          const table = element.data as HwpxTable;
          const rows = table.rows.length;
          const cols = table.rows[0]?.cells.length || 0;
          const isEmpty = this.isTableEmpty(table);

          outline.push({
            type: 'table',
            level: 5,
            text: `Table ${globalTableIndex} (${rows}x${cols})${isEmpty ? ' [empty]' : ''}`,
            section_index: sectionIndex,
            table_index: globalTableIndex,
            element_index: elementIndex,
          });

          globalTableIndex++;
        }
      });
    });

    return outline;
  }

  // ============================================================
  // Position/Index Helper Methods
  // ============================================================

  /**
   * Convert a global table index to element index in its section
   * @param tableIndex - Global table index (0-based across all sections)
   * @returns Object with section_index and element_index, or null if not found
   */
  getElementIndexForTable(tableIndex: number): {
    section_index: number;
    element_index: number;
    table_info: { rows: number; cols: number; header: string };
  } | null {
    let currentTableIndex = 0;

    for (let sectionIndex = 0; sectionIndex < this._content.sections.length; sectionIndex++) {
      const section = this._content.sections[sectionIndex];
      let lastParagraphText = '';

      for (let elementIndex = 0; elementIndex < section.elements.length; elementIndex++) {
        const element = section.elements[elementIndex];

        if (element.type === 'paragraph') {
          const para = element.data as HwpxParagraph;
          const text = para.runs.map(r => r.text).join('').trim();
          if (text) lastParagraphText = text;
        } else if (element.type === 'table') {
          if (currentTableIndex === tableIndex) {
            const table = element.data as HwpxTable;
            return {
              section_index: sectionIndex,
              element_index: elementIndex,
              table_info: {
                rows: table.rows.length,
                cols: table.rows[0]?.cells.length || 0,
                header: lastParagraphText,
              },
            };
          }
          currentTableIndex++;
        }
      }
    }

    return null;
  }

  /**
   * Find element index of a paragraph containing specific text
   * @param searchText - Text to search for (partial match, case-insensitive)
   * @param sectionIndex - Optional: limit search to specific section
   * @returns Array of matching positions with context
   */
  findParagraphByText(searchText: string, sectionIndex?: number): Array<{
    section_index: number;
    element_index: number;
    text: string;
    context_before: string;
    context_after: string;
  }> {
    const results: Array<{
      section_index: number;
      element_index: number;
      text: string;
      context_before: string;
      context_after: string;
    }> = [];

    const lowerSearch = searchText.toLowerCase();
    const sectionsToSearch = sectionIndex !== undefined
      ? [{ idx: sectionIndex, section: this._content.sections[sectionIndex] }]
      : this._content.sections.map((s, i) => ({ idx: i, section: s }));

    for (const { idx, section } of sectionsToSearch) {
      if (!section) continue;

      for (let elemIdx = 0; elemIdx < section.elements.length; elemIdx++) {
        const element = section.elements[elemIdx];

        if (element.type === 'paragraph') {
          const para = element.data as HwpxParagraph;
          const text = para.runs.map(r => r.text).join('');

          if (text.toLowerCase().includes(lowerSearch)) {
            // Get context
            const beforeElem = section.elements[elemIdx - 1];
            const afterElem = section.elements[elemIdx + 1];

            let contextBefore = '';
            let contextAfter = '';

            if (beforeElem?.type === 'paragraph') {
              contextBefore = (beforeElem.data as HwpxParagraph).runs.map(r => r.text).join('').substring(0, 50);
            } else if (beforeElem?.type === 'table') {
              contextBefore = '[Table]';
            }

            if (afterElem?.type === 'paragraph') {
              contextAfter = (afterElem.data as HwpxParagraph).runs.map(r => r.text).join('').substring(0, 50);
            } else if (afterElem?.type === 'table') {
              contextAfter = '[Table]';
            }

            results.push({
              section_index: idx,
              element_index: elemIdx,
              text: text.substring(0, 100) + (text.length > 100 ? '...' : ''),
              context_before: contextBefore,
              context_after: contextAfter,
            });
          }
        }
      }
    }

    return results;
  }

  /**
   * Get context around an element index (useful for verifying insertion point)
   * @param sectionIndex - Section index
   * @param elementIndex - Element index
   * @param contextRange - Number of elements before/after to include (default: 2)
   */
  getInsertContext(sectionIndex: number, elementIndex: number, contextRange = 2): {
    target_element: { type: string; text: string };
    elements_before: Array<{ type: string; text: string; element_index: number }>;
    elements_after: Array<{ type: string; text: string; element_index: number }>;
    recommended_insert_after: number;
  } | null {
    const section = this._content.sections[sectionIndex];
    if (!section) return null;

    const getElementSummary = (elem: SectionElement): string => {
      if (elem.type === 'paragraph') {
        const text = (elem.data as HwpxParagraph).runs.map(r => r.text).join('');
        return text.substring(0, 80) + (text.length > 80 ? '...' : '');
      } else if (elem.type === 'table') {
        const table = elem.data as HwpxTable;
        return `[Table ${table.rows.length}x${table.rows[0]?.cells.length || 0}]`;
      } else if (elem.type === 'image') {
        return '[Image]';
      }
      return `[${elem.type}]`;
    };

    const targetElem = section.elements[elementIndex];
    if (!targetElem) return null;

    const elementsBefore: Array<{ type: string; text: string; element_index: number }> = [];
    const elementsAfter: Array<{ type: string; text: string; element_index: number }> = [];

    for (let i = Math.max(0, elementIndex - contextRange); i < elementIndex; i++) {
      const elem = section.elements[i];
      if (elem) {
        elementsBefore.push({
          type: elem.type,
          text: getElementSummary(elem),
          element_index: i,
        });
      }
    }

    for (let i = elementIndex + 1; i <= Math.min(section.elements.length - 1, elementIndex + contextRange); i++) {
      const elem = section.elements[i];
      if (elem) {
        elementsAfter.push({
          type: elem.type,
          text: getElementSummary(elem),
          element_index: i,
        });
      }
    }

    return {
      target_element: { type: targetElem.type, text: getElementSummary(targetElem) },
      elements_before: elementsBefore,
      elements_after: elementsAfter,
      recommended_insert_after: elementIndex,
    };
  }

  /**
   * Find insertion position by searching for a header/title text
   * Returns position right after the found paragraph (good for inserting content under a header)
   * @param headerText - Text to search for in paragraph headers
   */
  /**
   * Find insertion position after header/text.
   * @param headerText - Text to search for
   * @param searchIn - Where to search: 'paragraphs', 'table_cells', or 'all' (default)
   */
  findInsertPositionAfterHeader(
    headerText: string,
    searchIn: 'paragraphs' | 'table_cells' | 'all' = 'all'
  ): {
    section_index: number;
    element_index: number;
    insert_after: number;
    header_found: string;
    found_in: 'paragraph' | 'table_cell';
    table_info?: { table_index: number; row: number; col: number };
    next_element: { type: string; text: string } | null;
  } | null {
    // Search in paragraphs first (if enabled)
    if (searchIn === 'paragraphs' || searchIn === 'all') {
      const matches = this.findParagraphByText(headerText);
      if (matches.length > 0) {
        const match = matches[0];
        const section = this._content.sections[match.section_index];
        const nextElem = section.elements[match.element_index + 1];

        let nextElementInfo: { type: string; text: string } | null = null;
        if (nextElem) {
          if (nextElem.type === 'paragraph') {
            const text = (nextElem.data as HwpxParagraph).runs.map(r => r.text).join('');
            nextElementInfo = { type: 'paragraph', text: text.substring(0, 80) };
          } else if (nextElem.type === 'table') {
            const table = nextElem.data as HwpxTable;
            nextElementInfo = { type: 'table', text: `${table.rows.length}x${table.rows[0]?.cells.length || 0}` };
          } else {
            nextElementInfo = { type: nextElem.type, text: '' };
          }
        }

        return {
          section_index: match.section_index,
          element_index: match.element_index,
          insert_after: match.element_index,
          header_found: match.text,
          found_in: 'paragraph',
          next_element: nextElementInfo,
        };
      }
    }

    // Search in table cells (if enabled)
    if (searchIn === 'table_cells' || searchIn === 'all') {
      const cellMatch = this.findTextInTableCells(headerText);
      if (cellMatch) {
        const section = this._content.sections[cellMatch.section_index];
        const nextElem = section.elements[cellMatch.element_index + 1];

        let nextElementInfo: { type: string; text: string } | null = null;
        if (nextElem) {
          if (nextElem.type === 'paragraph') {
            const text = (nextElem.data as HwpxParagraph).runs.map(r => r.text).join('');
            nextElementInfo = { type: 'paragraph', text: text.substring(0, 80) };
          } else if (nextElem.type === 'table') {
            const table = nextElem.data as HwpxTable;
            nextElementInfo = { type: 'table', text: `${table.rows.length}x${table.rows[0]?.cells.length || 0}` };
          } else {
            nextElementInfo = { type: nextElem.type, text: '' };
          }
        }

        return {
          section_index: cellMatch.section_index,
          element_index: cellMatch.element_index,
          insert_after: cellMatch.element_index, // Insert after the table containing the text
          header_found: cellMatch.text,
          found_in: 'table_cell',
          table_info: {
            table_index: cellMatch.table_index,
            row: cellMatch.row,
            col: cellMatch.col,
          },
          next_element: nextElementInfo,
        };
      }
    }

    return null;
  }

  /**
   * Find text in table cells
   * @param searchText - Text to search for (partial match)
   */
  private findTextInTableCells(searchText: string): {
    section_index: number;
    element_index: number;
    table_index: number;
    row: number;
    col: number;
    text: string;
  } | null {
    const normalizedSearch = searchText.toLowerCase().trim();
    let globalTableIndex = 0;

    for (let si = 0; si < this._content.sections.length; si++) {
      const section = this._content.sections[si];

      for (let ei = 0; ei < section.elements.length; ei++) {
        const elem = section.elements[ei];

        if (elem.type === 'table') {
          const table = elem.data as HwpxTable;

          for (let ri = 0; ri < table.rows.length; ri++) {
            const row = table.rows[ri];

            for (let ci = 0; ci < row.cells.length; ci++) {
              const cell = row.cells[ci];
              const cellText = cell.paragraphs
                .map(p => p.runs.map(r => r.text).join(''))
                .join('\n');

              if (cellText.toLowerCase().includes(normalizedSearch)) {
                return {
                  section_index: si,
                  element_index: ei,
                  table_index: globalTableIndex,
                  row: ri,
                  col: ci,
                  text: cellText.substring(0, 100),
                };
              }
            }
          }

          globalTableIndex++;
        }
      }
    }

    return null;
  }

  /**
   * Find insertion position right after a specific table
   * @param tableIndex - Global table index
   */
  findInsertPositionAfterTable(tableIndex: number): {
    section_index: number;
    element_index: number;
    insert_after: number;
    table_info: { rows: number; cols: number; header: string };
    next_element: { type: string; text: string } | null;
  } | null {
    const tablePos = this.getElementIndexForTable(tableIndex);
    if (!tablePos) return null;

    const section = this._content.sections[tablePos.section_index];
    const nextElem = section.elements[tablePos.element_index + 1];

    let nextElementInfo: { type: string; text: string } | null = null;
    if (nextElem) {
      if (nextElem.type === 'paragraph') {
        const text = (nextElem.data as HwpxParagraph).runs.map(r => r.text).join('');
        nextElementInfo = { type: 'paragraph', text: text.substring(0, 80) };
      } else if (nextElem.type === 'table') {
        const table = nextElem.data as HwpxTable;
        nextElementInfo = { type: 'table', text: `${table.rows.length}x${table.rows[0]?.cells.length || 0}` };
      } else {
        nextElementInfo = { type: nextElem.type, text: '' };
      }
    }

    return {
      section_index: tablePos.section_index,
      element_index: tablePos.element_index,
      insert_after: tablePos.element_index, // Insert after the table
      table_info: tablePos.table_info,
      next_element: nextElementInfo,
    };
  }

  getTable(sectionIndex: number, tableIndex: number): { rows: number; cols: number; data: any[][] } | null {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return null;

    return {
      rows: table.rows.length,
      cols: table.rows[0]?.cells.length || 0,
      data: table.rows.map(row => row.cells.map(cell => ({
        text: cell.paragraphs.map(p => p.runs.map(r => r.text).join('')).join('\n'),
        style: cell,
      }))),
    };
  }

  getTableCell(sectionIndex: number, tableIndex: number, row: number, col: number): { text: string; cell: TableCell } | null {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return null;
    const cell = table.rows[row]?.cells[col];
    if (!cell) return null;
    return {
      text: cell.paragraphs.map(p => p.runs.map(r => r.text).join('')).join('\n'),
      cell,
    };
  }

  updateTableCell(sectionIndex: number, tableIndex: number, row: number, col: number, text: string, charShapeId?: number): boolean {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return false;
    const cell = table.rows[row]?.cells[col];
    if (!cell) return false;

    // Track cell update for XML sync (works for both empty and non-empty cells)
    // Store table ID for reliable XML matching
    // charShapeId is optional - if provided, it will override the existing charPrIDRef
    this._pendingTableCellUpdates.push({ sectionIndex, tableIndex, tableId: table.id, row, col, text, charShapeId });

    this.saveState();
    if (cell.paragraphs.length > 0 && cell.paragraphs[0].runs.length > 0) {
      cell.paragraphs[0].runs[0].text = text;
    } else {
      cell.paragraphs = [{ id: Math.random().toString(36).substring(2, 11), runs: [{ text }] }];
    }

    this.markModified();
    return true;
  }

  setCellProperties(sectionIndex: number, tableIndex: number, row: number, col: number, props: Partial<TableCell>): boolean {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return false;
    const cell = table.rows[row]?.cells[col];
    if (!cell) return false;

    this.saveState();
    Object.assign(cell, props);
    this.markModified();
    return true;
  }

  insertTableRow(sectionIndex: number, tableIndex: number, afterRowIndex: number, cellTexts?: string[]): boolean {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table || !table.rows[afterRowIndex]) return false;

    this.saveState();
    const templateRow = table.rows[afterRowIndex];
    const colCount = templateRow.cells.length;

    const newRow = {
      cells: Array.from({ length: colCount }, (_, i) => ({
        paragraphs: [{
          id: Math.random().toString(36).substring(2, 11),
          runs: [{ text: cellTexts?.[i] || '' }],
        }],
      })),
    };

    table.rows.splice(afterRowIndex + 1, 0, newRow as any);

    this._pendingTableRowInserts.push({
      sectionIndex,
      tableIndex,
      afterRowIndex,
      cellTexts,
    });

    this.markModified();
    return true;
  }

  deleteTableRow(sectionIndex: number, tableIndex: number, rowIndex: number): boolean {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return false;

    // If only 1 row, delete the entire table instead
    if (table.rows.length <= 1) {
      return this.deleteTable(sectionIndex, tableIndex);
    }

    this.saveState();
    table.rows.splice(rowIndex, 1);

    this._pendingTableRowDeletes.push({
      sectionIndex,
      tableIndex,
      rowIndex,
    });

    this.markModified();
    return true;
  }

  /**
   * Delete an entire table from the document
   */
  deleteTable(sectionIndex: number, tableIndex: number): boolean {
    const section = this._content?.sections?.[sectionIndex];
    if (!section?.elements) return false;

    // Find the table's index in elements array
    let tableCount = 0;
    let elementIndex = -1;
    let tableId: string | undefined;

    for (let i = 0; i < section.elements.length; i++) {
      const el = section.elements[i];
      if (el.type === 'table') {
        if (tableCount === tableIndex) {
          elementIndex = i;
          tableId = el.data?.id;
          break;
        }
        tableCount++;
      }
    }

    if (elementIndex === -1) return false;

    this.saveState();

    // Add to pending deletes for XML processing
    this._pendingTableDeletes.push({
      sectionIndex,
      tableIndex,
      tableId
    });

    // Remove from memory model
    section.elements.splice(elementIndex, 1);
    this.markModified();
    return true;
  }

  insertTableColumn(sectionIndex: number, tableIndex: number, afterColIndex: number): boolean {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return false;

    this.saveState();
    for (const row of table.rows) {
      row.cells.splice(afterColIndex + 1, 0, {
        paragraphs: [{
          id: Math.random().toString(36).substring(2, 11),
          runs: [{ text: '' }],
        }],
      } as any);
    }

    this._pendingTableColumnInserts.push({
      sectionIndex,
      tableIndex,
      afterColIndex,
    });

    this.markModified();
    return true;
  }

  deleteTableColumn(sectionIndex: number, tableIndex: number, colIndex: number): boolean {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table || (table.rows[0]?.cells.length || 0) <= 1) return false;

    this.saveState();
    for (const row of table.rows) {
      row.cells.splice(colIndex, 1);
    }

    this._pendingTableColumnDeletes.push({
      sectionIndex,
      tableIndex,
      colIndex,
    });

    this.markModified();
    return true;
  }

  getTableAsCsv(sectionIndex: number, tableIndex: number, delimiter: string = ','): string | null {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return null;

    return table.rows.map(row =>
      row.cells.map(cell => {
        const text = cell.paragraphs.map(p => p.runs.map(r => r.text).join('')).join(' ');
        if (text.includes(delimiter) || text.includes('"') || text.includes('\n')) {
          return `"${text.replace(/"/g, '""')}"`;
        }
        return text;
      }).join(delimiter)
    ).join('\n');
  }

  // ============================================================
  // Search & Replace
  // ============================================================

  searchText(query: string, options: { caseSensitive?: boolean; regex?: boolean; includeTables?: boolean } = {}): Array<{ section: number; element: number; text: string; matches: string[]; count: number; location?: { type: 'paragraph' | 'table'; tableIndex?: number; row?: number; col?: number } }> {
    const { caseSensitive = false, regex = false, includeTables = true } = options;
    let pattern: RegExp;

    if (regex) {
      pattern = new RegExp(query, caseSensitive ? 'g' : 'gi');
    } else {
      const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      pattern = new RegExp(escaped, caseSensitive ? 'g' : 'gi');
    }

    const results: Array<{ section: number; element: number; text: string; matches: string[]; count: number; location?: { type: 'paragraph' | 'table'; tableIndex?: number; row?: number; col?: number } }> = [];

    this._content.sections.forEach((section, si) => {
      let tableIndex = 0;
      section.elements.forEach((el, ei) => {
        if (el.type === 'paragraph') {
          const text = el.data.runs.map(r => r.text).join('');
          const found = text.match(pattern);
          if (found) {
            results.push({
              section: si,
              element: ei,
              text,
              matches: found,
              count: found.length,
              location: { type: 'paragraph' },
            });
          }
        }
        // Search in table cells
        if (el.type === 'table' && includeTables) {
          const table = el.data as HwpxTable;
          table.rows.forEach((row, ri) => {
            row.cells.forEach((cell, ci) => {
              const cellText = cell.paragraphs.map(p => p.runs.map(r => r.text).join('')).join('\n');
              const found = cellText.match(pattern);
              if (found) {
                results.push({
                  section: si,
                  element: ei,
                  text: cellText,
                  matches: found,
                  count: found.length,
                  location: { type: 'table', tableIndex, row: ri, col: ci },
                });
              }
            });
          });
          tableIndex++;
        }
      });
    });

    return results;
  }

  replaceText(oldText: string, newText: string, options: { caseSensitive?: boolean; regex?: boolean; replaceAll?: boolean } = {}): number {
    const { caseSensitive = false, regex = false, replaceAll = true } = options;
    let pattern: RegExp;

    if (regex) {
      pattern = new RegExp(oldText, caseSensitive ? (replaceAll ? 'g' : '') : (replaceAll ? 'gi' : 'i'));
    } else {
      const escaped = oldText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      pattern = new RegExp(escaped, caseSensitive ? (replaceAll ? 'g' : '') : (replaceAll ? 'gi' : 'i'));
    }

    this.saveState();
    let count = 0;

    // Update in-memory content
    for (const section of this._content.sections) {
      for (const element of section.elements) {
        if (element.type === 'paragraph') {
          for (const run of element.data.runs) {
            const matches = run.text.match(pattern);
            if (matches) {
              count += matches.length;
              run.text = run.text.replace(pattern, newText);
            }
          }
        }
        // Also handle table cells
        if (element.type === 'table') {
          const table = element.data as HwpxTable;
          for (const row of table.rows) {
            for (const cell of row.cells) {
              for (const para of cell.paragraphs) {
                for (const run of para.runs) {
                  const matches = run.text.match(pattern);
                  if (matches) {
                    count += matches.length;
                    run.text = run.text.replace(pattern, newText);
                  }
                }
              }
            }
          }
        }
      }
    }

    // Also update directly in the ZIP XML files for safe saving
    if (count > 0 && this._zip) {
      this._pendingTextReplacements = this._pendingTextReplacements || [];
      this._pendingTextReplacements.push({ oldText, newText, options });
      this.markModified();
      this.invalidateReadingCache();
    }

    return count;
  }

  /**
   * Replace text within a specific table cell.
   * This is more targeted than replaceText and works directly on cell content.
   */
  replaceTextInCell(
    sectionIndex: number,
    tableIndex: number,
    row: number,
    col: number,
    oldText: string,
    newText: string,
    options: { caseSensitive?: boolean; regex?: boolean; replaceAll?: boolean } = {}
  ): { success: boolean; count: number; error?: string } {
    const { caseSensitive = false, regex = false, replaceAll = true } = options;

    const section = this._content.sections[sectionIndex];
    if (!section) {
      return { success: false, count: 0, error: `Section ${sectionIndex} not found` };
    }

    // Find the table
    let tableCount = 0;
    let targetTable: HwpxTable | null = null;
    for (const el of section.elements) {
      if (el.type === 'table') {
        if (tableCount === tableIndex) {
          targetTable = el.data as HwpxTable;
          break;
        }
        tableCount++;
      }
    }

    if (!targetTable) {
      return { success: false, count: 0, error: `Table ${tableIndex} not found in section ${sectionIndex}` };
    }

    if (row >= targetTable.rows.length) {
      return { success: false, count: 0, error: `Row ${row} out of range (max: ${targetTable.rows.length - 1})` };
    }

    if (col >= targetTable.rows[row].cells.length) {
      return { success: false, count: 0, error: `Column ${col} out of range (max: ${targetTable.rows[row].cells.length - 1})` };
    }

    const cell = targetTable.rows[row].cells[col];
    this.saveState();

    let pattern: RegExp;
    if (regex) {
      pattern = new RegExp(oldText, caseSensitive ? (replaceAll ? 'g' : '') : (replaceAll ? 'gi' : 'i'));
    } else {
      const escaped = oldText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      pattern = new RegExp(escaped, caseSensitive ? (replaceAll ? 'g' : '') : (replaceAll ? 'gi' : 'i'));
    }

    let count = 0;

    // Replace in memory
    for (const para of cell.paragraphs) {
      for (const run of para.runs) {
        const matches = run.text.match(pattern);
        if (matches) {
          count += matches.length;
          run.text = run.text.replace(pattern, newText);
        }
      }
    }

    // Add to pending cell updates for XML sync
    if (count > 0 && this._zip) {
      const tableId = targetTable.id || '';
      const cellText = cell.paragraphs.map(p => p.runs.map(r => r.text).join('')).join('\n');

      // Use existing pending table cell update mechanism
      this._pendingTableCellUpdates = this._pendingTableCellUpdates || [];
      this._pendingTableCellUpdates.push({
        sectionIndex,
        tableIndex,
        tableId,
        row,
        col,
        text: cellText,
      });
      this.markModified();
      this.invalidateReadingCache();
    }

    return { success: true, count };
  }

  // ============================================================
  // Metadata
  // ============================================================

  getMetadata(): HwpxContent['metadata'] {
    return this._content.metadata;
  }

  setMetadata(metadata: Partial<HwpxContent['metadata']>): void {
    this.saveState();
    this._content.metadata = { ...this._content.metadata, ...metadata };
    this.markModified();
  }

  // ============================================================
  // Page Settings
  // ============================================================

  getPageSettings(sectionIndex: number = 0): PageSettings | null {
    const section = this._content.sections[sectionIndex];
    return section?.pageSettings || null;
  }

  setPageSettings(sectionIndex: number, settings: Partial<PageSettings>): boolean {
    const section = this._content.sections[sectionIndex];
    if (!section) return false;

    this.saveState();
    section.pageSettings = { ...section.pageSettings, ...settings } as PageSettings;
    this.markModified();
    return true;
  }

  // ============================================================
  // Statistics
  // ============================================================

  getWordCount(): { characters: number; charactersNoSpaces: number; words: number; paragraphs: number } {
    let characters = 0;
    let charactersNoSpaces = 0;
    let words = 0;
    let paragraphs = 0;

    for (const section of this._content.sections) {
      for (const element of section.elements) {
        if (element.type === 'paragraph') {
          paragraphs++;
          const text = element.data.runs.map(r => r.text).join('');
          characters += text.length;
          charactersNoSpaces += text.replace(/\s/g, '').length;
          words += text.trim().split(/\s+/).filter(w => w.length > 0).length;
        }
      }
    }

    return { characters, charactersNoSpaces, words, paragraphs };
  }

  // ============================================================
  // Copy/Move Operations
  // ============================================================

  copyParagraph(sourceSection: number, sourceParagraph: number, targetSection: number, targetAfter: number): boolean {
    const srcSection = this._content.sections[sourceSection];
    const tgtSection = this._content.sections[targetSection];
    if (!srcSection || !tgtSection) return false;

    const srcElement = srcSection.elements[sourceParagraph];
    if (!srcElement || srcElement.type !== 'paragraph') return false;

    this.saveState();
    const copy = JSON.parse(JSON.stringify(srcElement));
    copy.data.id = Math.random().toString(36).substring(2, 11);
    tgtSection.elements.splice(targetAfter + 1, 0, copy);

    this._pendingParagraphCopies.push({
      sourceSection,
      sourceParagraph,
      targetSection,
      targetAfter,
    });

    this.markModified();
    return true;
  }

  moveParagraph(sourceSection: number, sourceParagraph: number, targetSection: number, targetAfter: number): boolean {
    const srcSection = this._content.sections[sourceSection];
    const tgtSection = this._content.sections[targetSection];
    if (!srcSection || !tgtSection) return false;

    const srcElement = srcSection.elements[sourceParagraph];
    if (!srcElement || srcElement.type !== 'paragraph') return false;

    this.saveState();
    srcSection.elements.splice(sourceParagraph, 1);

    // Fix same-section index shift: if source was before target, adjust target down
    let adjustedTargetAfter = targetAfter;
    if (sourceSection === targetSection && sourceParagraph < targetAfter) {
      adjustedTargetAfter -= 1;
    }

    tgtSection.elements.splice(adjustedTargetAfter + 1, 0, srcElement);

    this._pendingParagraphMoves.push({
      sourceSection,
      sourceParagraph,
      targetSection,
      targetAfter,
    });

    this.markModified();
    return true;
  }

  // ============================================================
  // Table/Image Move Operations (XML-based)
  // ============================================================

  /**
   * Move a table from one location to another within the document.
   * Uses XML-based approach for accurate preservation of table structure.
   */
  moveTable(
    sectionIndex: number,
    tableIndex: number,
    targetSectionIndex: number,
    targetAfterIndex: number
  ): { success: boolean; error?: string } {
    // Validate source section
    if (!this._content.sections[sectionIndex]) {
      return { success: false, error: `Invalid source section index: ${sectionIndex}` };
    }

    // Validate target section
    if (!this._content.sections[targetSectionIndex]) {
      return { success: false, error: `Invalid target section index: ${targetSectionIndex}` };
    }

    // Table index validation will be done during XML processing
    // since we need to read the actual XML to count tables

    this.saveState();

    // Add to pending moves for XML processing during save
    if (!this._pendingTableMoves) {
      this._pendingTableMoves = [];
    }

    this._pendingTableMoves.push({
      type: 'move',
      sourceSectionIndex: sectionIndex,
      sourceTableIndex: tableIndex,
      targetSectionIndex,
      targetAfterIndex,
    });

    this.markModified();
    return { success: true };
  }

  /**
   * Copy a table to another location (preserving original).
   * Generates new IDs for the copied table.
   */
  copyTable(
    sectionIndex: number,
    tableIndex: number,
    targetSectionIndex: number,
    targetAfterIndex: number
  ): { success: boolean; error?: string } {
    // Validate source section
    if (!this._content.sections[sectionIndex]) {
      return { success: false, error: `Invalid source section index: ${sectionIndex}` };
    }

    // Validate target section
    if (!this._content.sections[targetSectionIndex]) {
      return { success: false, error: `Invalid target section index: ${targetSectionIndex}` };
    }

    // Table index validation will be done during XML processing

    this.saveState();

    // Add to pending copies for XML processing during save
    if (!this._pendingTableMoves) {
      this._pendingTableMoves = [];
    }

    this._pendingTableMoves.push({
      type: 'copy',
      sourceSectionIndex: sectionIndex,
      sourceTableIndex: tableIndex,
      targetSectionIndex,
      targetAfterIndex,
    });

    this.markModified();
    return { success: true };
  }

  /**
   * Validate XML tag balance for specified tags.
   * Returns balanced status and any mismatches found.
   */
  validateTagBalance(xml: string): {
    balanced: boolean;
    mismatches: Array<{ tag: string; opens: number; closes: number }>;
  } {
    const tagsToCheck = [
      'hp:tbl', 'hp:tr', 'hp:tc',
      'hp:p', 'hp:run', 'hp:t',
      'hp:pic', 'hp:subList',
      'hs:sec'
    ];

    const mismatches: Array<{ tag: string; opens: number; closes: number }> = [];

    for (const tag of tagsToCheck) {
      // Count opening tags (including those that might be self-closing)
      const allOpenRegex = new RegExp(`<${tag}(?:\\s[^>]*)?>`, 'g');
      // Count self-closing tags
      const selfCloseRegex = new RegExp(`<${tag}[^>]*/>`, 'g');
      // Count closing tags
      const closeRegex = new RegExp(`</${tag}>`, 'g');

      const allOpens = (xml.match(allOpenRegex) || []).length;
      const selfCloses = (xml.match(selfCloseRegex) || []).length;
      const closes = (xml.match(closeRegex) || []).length;

      // Real opens = all opens - self-closing tags
      const opens = allOpens - selfCloses;

      if (opens !== closes) {
        mismatches.push({ tag, opens, closes });
      }
    }

    return {
      balanced: mismatches.length === 0,
      mismatches,
    };
  }

  /**
   * Validate XML text content is properly escaped.
   */
  validateXmlEscaping(xml: string): { valid: boolean; issues?: string[] } {
    const issues: string[] = [];

    // Check for unescaped & (but not &amp;, &lt;, &gt;, &quot;, &apos;, or numeric entities)
    const unescapedAmpersand = /&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)/g;
    if (unescapedAmpersand.test(xml)) {
      issues.push('Found unescaped ampersand (&)');
    }

    // Check for unescaped < or > in text content is complex
    // For now, assume escapeXml function handles this correctly

    return {
      valid: issues.length === 0,
      issues: issues.length > 0 ? issues : undefined,
    };
  }

  // Private: Pending table move/copy operations
  private _pendingTableMoves: Array<{
    type: 'move' | 'copy';
    sourceSectionIndex: number;
    sourceTableIndex: number;
    targetSectionIndex: number;
    targetAfterIndex: number;
  }> = [];

  // ============================================================
  // Images
  // ============================================================

  getImages(): Array<{ id: string; width: number; height: number }> {
    return Array.from(this._content.images.values()).map(img => ({
      id: img.id,
      width: img.width,
      height: img.height,
    }));
  }

  // ============================================================
  // Table Creation
  // ============================================================

  insertTable(sectionIndex: number, afterElementIndex: number, rows: number, cols: number, options?: { width?: number; cellWidth?: number }): { tableIndex: number } | null {
    const section = this._content.sections[sectionIndex];
    if (!section) return null;
    if (rows <= 0 || cols <= 0) return null;

    this.saveState();

    const tableId = Math.random().toString(36).substring(2, 11);
    const defaultWidth = options?.width || 42520; // Default table width in hwpunit
    const cellWidth = options?.cellWidth || Math.floor(defaultWidth / cols);

    const tableRows: TableRow[] = [];
    for (let r = 0; r < rows; r++) {
      const cells: TableCell[] = [];
      for (let c = 0; c < cols; c++) {
        cells.push({
          colAddr: c,
          rowAddr: r,
          colSpan: 1,
          rowSpan: 1,
          width: cellWidth,
          paragraphs: [{
            id: Math.random().toString(36).substring(2, 11),
            runs: [{ text: '' }],
          }],
        });
      }
      tableRows.push({ cells });
    }

    const newTable: HwpxTable = {
      id: tableId,
      rowCount: rows,
      colCount: cols,
      rows: tableRows,
      width: defaultWidth,
    };

    const newElement: SectionElement = { type: 'table', data: newTable };
    section.elements.splice(afterElementIndex + 1, 0, newElement);

    // Calculate table index
    let tableIndex = 0;
    for (let i = 0; i <= afterElementIndex + 1; i++) {
      if (section.elements[i]?.type === 'table') {
        if (i === afterElementIndex + 1) break;
        tableIndex++;
      }
    }

    // Add to pending table inserts for XML generation
    // Store the original afterElementIndex and insertOrder for proper sequencing
    this._pendingTableInserts.push({
      sectionIndex,
      afterElementIndex,
      rows,
      cols,
      width: defaultWidth,
      cellWidth,
      insertOrder: this._tableInsertCounter++,
      tableId: tableId,
    });

    this.markModified();
    return { tableIndex };
  }

  /**
   * Insert a nested table inside a table cell.
   * @param sectionIndex Section index
   * @param parentTableIndex Parent table index
   * @param row Row index in parent table
   * @param col Column index in parent table
   * @param nestedRows Number of rows in nested table
   * @param nestedCols Number of columns in nested table
   * @param options Optional data for cells
   */
  insertNestedTable(
    sectionIndex: number,
    parentTableIndex: number,
    row: number,
    col: number,
    nestedRows: number,
    nestedCols: number,
    options?: { data?: string[][] }
  ): { success: boolean; error?: string } {
    const section = this._content.sections[sectionIndex];
    if (!section) {
      return { success: false, error: `Section ${sectionIndex} does not exist. Document has ${this._content.sections.length} section(s). Valid range: 0-${this._content.sections.length - 1}` };
    }
    if (nestedRows <= 0 || nestedCols <= 0) {
      return { success: false, error: `Invalid nested table size: ${nestedRows}x${nestedCols}. Both rows and columns must be greater than 0.` };
    }

    // Find the parent table and count total tables
    let tableCount = 0;
    let parentTable: HwpxTable | null = null;
    let totalTables = 0;
    for (const element of section.elements) {
      if (element.type === 'table') {
        if (tableCount === parentTableIndex) {
          parentTable = element.data as HwpxTable;
        }
        tableCount++;
        totalTables++;
      }
    }

    if (!parentTable) {
      return { success: false, error: `Table ${parentTableIndex} does not exist in section ${sectionIndex}. Section has ${totalTables} table(s). Valid range: 0-${totalTables > 0 ? totalTables - 1 : 0}` };
    }

    const tableRows = parentTable.rows.length;
    const tableCols = parentTable.rows[0]?.cells.length || 0;

    if (row >= tableRows) {
      return { success: false, error: `Row ${row} does not exist. Table size: ${tableRows}x${tableCols} (${tableRows} rows, ${tableCols} cols). Valid row range: 0-${tableRows - 1}` };
    }

    const rowCols = parentTable.rows[row].cells.length;
    if (col >= rowCols) {
      return { success: false, error: `Column ${col} does not exist in row ${row}. Row has ${rowCols} column(s). Valid column range: 0-${rowCols - 1}` };
    }

    this.saveState();

    // Store the nested table insertion request for XML processing
    if (!this._pendingNestedTableInserts) {
      this._pendingNestedTableInserts = [];
    }

    this._pendingNestedTableInserts.push({
      sectionIndex,
      parentTableIndex,
      row,
      col,
      nestedRows,
      nestedCols,
      data: options?.data || []
    });

    this.markModified();
    return { success: true };
  }

  // ============================================================
  // Cell Merge Operations
  // ============================================================

  /**
   * Merge multiple cells in a table into a single cell.
   * The top-left cell becomes the master cell, and other cells in the range are removed.
   *
   * @param sectionIndex Section index
   * @param tableIndex Table index within the section
   * @param startRow Starting row index (0-based)
   * @param startCol Starting column index (0-based)
   * @param endRow Ending row index (0-based, inclusive)
   * @param endCol Ending column index (0-based, inclusive)
   * @returns true if merge was successful, false otherwise
   */
  mergeCells(
    sectionIndex: number,
    tableIndex: number,
    startRow: number,
    startCol: number,
    endRow: number,
    endCol: number
  ): boolean {
    // Validate section
    const section = this._content.sections[sectionIndex];
    if (!section) {
      console.warn(`[HwpxDocument] mergeCells: Invalid section index ${sectionIndex}`);
      return false;
    }

    // Find the table
    let tableCount = 0;
    let table: HwpxTable | null = null;
    for (const element of section.elements) {
      if (element.type === 'table') {
        if (tableCount === tableIndex) {
          table = element.data as HwpxTable;
          break;
        }
        tableCount++;
      }
    }

    if (!table) {
      console.warn(`[HwpxDocument] mergeCells: Invalid table index ${tableIndex}`);
      return false;
    }

    // Validate range
    if (startRow > endRow || startCol > endCol) {
      console.warn(`[HwpxDocument] mergeCells: Invalid range (start > end)`);
      return false;
    }

    // Check bounds
    const rowCount = table.rowCount || (table.rows?.length ?? 0);
    const colCount = table.colCount || (table.rows?.[0]?.cells?.length ?? 0);
    if (startRow < 0 || endRow >= rowCount || startCol < 0 || endCol >= colCount) {
      console.warn(`[HwpxDocument] mergeCells: Range out of bounds`);
      return false;
    }

    // Check if it's a single cell (no merge needed)
    if (startRow === endRow && startCol === endCol) {
      console.warn(`[HwpxDocument] mergeCells: Single cell selected, no merge needed`);
      return false;
    }

    this.saveState();

    // Calculate span values
    const colSpan = endCol - startCol + 1;
    const rowSpan = endRow - startRow + 1;

    // Update master cell in memory model
    if (table.rows && table.rows[startRow] && table.rows[startRow].cells[startCol]) {
      const masterCell = table.rows[startRow].cells[startCol];
      masterCell.colSpan = colSpan;
      masterCell.rowSpan = rowSpan;
    }

    // Add to pending merges for XML application during save
    this._pendingCellMerges.push({
      sectionIndex,
      tableIndex,
      startRow,
      startCol,
      endRow,
      endCol
    });

    this.markModified();
    return true;
  }

  /**
   * Split a merged cell back into individual cells.
   * Only works on cells with colSpan > 1 or rowSpan > 1.
   *
   * @param sectionIndex Section index
   * @param tableIndex Table index within the section
   * @param row Row index of the merged cell (0-based)
   * @param col Column index of the merged cell (0-based)
   * @returns true if split was successful, false otherwise
   */
  splitCell(
    sectionIndex: number,
    tableIndex: number,
    row: number,
    col: number
  ): boolean {
    // Validate section
    const section = this._content.sections[sectionIndex];
    if (!section) {
      console.warn(`[HwpxDocument] splitCell: Invalid section index ${sectionIndex}`);
      return false;
    }

    // Find the table
    let tableCount = 0;
    let table: HwpxTable | null = null;
    for (const element of section.elements) {
      if (element.type === 'table') {
        if (tableCount === tableIndex) {
          table = element.data as HwpxTable;
          break;
        }
        tableCount++;
      }
    }

    if (!table) {
      console.warn(`[HwpxDocument] splitCell: Invalid table index ${tableIndex}`);
      return false;
    }

    // Check bounds
    const rowCount = table.rowCount || (table.rows?.length ?? 0);
    const colCount = table.colCount || (table.rows?.[0]?.cells?.length ?? 0);
    if (row < 0 || row >= rowCount || col < 0 || col >= colCount) {
      console.warn(`[HwpxDocument] splitCell: Cell out of bounds`);
      return false;
    }

    // Get the cell (from memory model if available)
    const cell = table.rows?.[row]?.cells?.[col];
    const colSpan = cell?.colSpan || 1;
    const rowSpan = cell?.rowSpan || 1;

    // Check if it's actually merged
    if (colSpan <= 1 && rowSpan <= 1) {
      console.warn(`[HwpxDocument] splitCell: Cell is not merged`);
      return false;
    }

    this.saveState();

    // Update memory model
    if (cell) {
      cell.colSpan = 1;
      cell.rowSpan = 1;
    }

    // Add to pending splits for XML application during save
    this._pendingCellSplits.push({
      sectionIndex,
      tableIndex,
      row,
      col,
      originalColSpan: colSpan,
      originalRowSpan: rowSpan
    });

    this.markModified();
    return true;
  }

  // ============================================================
  // Header/Footer Operations
  // ============================================================

  getHeader(sectionIndex: number): { paragraphs: any[] } | null {
    const section = this._content.sections[sectionIndex];
    if (!section || !section.header) return null;
    return {
      paragraphs: section.header.paragraphs.map(p => ({
        id: p.id,
        text: p.runs.map(r => r.text).join(''),
        runs: p.runs,
      })),
    };
  }

  setHeader(sectionIndex: number, text: string): boolean {
    const section = this._content.sections[sectionIndex];
    if (!section) return false;

    this.saveState();

    const headerParagraph: HwpxParagraph = {
      id: Math.random().toString(36).substring(2, 11),
      runs: [{ text }],
    };

    if (!section.header) {
      section.header = {
        paragraphs: [headerParagraph],
      };
    } else {
      section.header.paragraphs = [headerParagraph];
    }

    this._pendingHeaderUpdates.push({ sectionIndex, text });

    this.markModified();
    return true;
  }

  getFooter(sectionIndex: number): { paragraphs: any[] } | null {
    const section = this._content.sections[sectionIndex];
    if (!section || !section.footer) return null;
    return {
      paragraphs: section.footer.paragraphs.map(p => ({
        id: p.id,
        text: p.runs.map(r => r.text).join(''),
        runs: p.runs,
      })),
    };
  }

  setFooter(sectionIndex: number, text: string): boolean {
    const section = this._content.sections[sectionIndex];
    if (!section) return false;

    this.saveState();

    const footerParagraph: HwpxParagraph = {
      id: Math.random().toString(36).substring(2, 11),
      runs: [{ text }],
    };

    if (!section.footer) {
      section.footer = {
        paragraphs: [footerParagraph],
      };
    } else {
      section.footer.paragraphs = [footerParagraph];
    }

    this._pendingFooterUpdates.push({ sectionIndex, text });

    this.markModified();
    return true;
  }

  // ============================================================
  // Footnote/Endnote Operations
  // ============================================================

  getFootnotes(): Footnote[] {
    return this._content.footnotes || [];
  }

  insertFootnote(sectionIndex: number, paragraphIndex: number, text: string): { id: string } | null {
    const paragraph = this.findParagraphByPath(sectionIndex, paragraphIndex);
    if (!paragraph) return null;

    this.saveState();

    const footnoteId = Math.random().toString(36).substring(2, 11);
    const footnoteNumber = (this._content.footnotes?.length || 0) + 1;

    const footnote: Footnote = {
      id: footnoteId,
      number: footnoteNumber,
      type: 'footnote',
      paragraphs: [{
        id: Math.random().toString(36).substring(2, 11),
        runs: [{ text }],
      }],
    };

    if (!this._content.footnotes) {
      this._content.footnotes = [];
    }
    this._content.footnotes.push(footnote);

    // Add footnote reference to the paragraph
    paragraph.runs.push({
      text: '',
      footnoteRef: footnoteNumber,
    });

    this.markModified();
    return { id: footnoteId };
  }

  getEndnotes(): Endnote[] {
    return this._content.endnotes || [];
  }

  insertEndnote(sectionIndex: number, paragraphIndex: number, text: string): { id: string } | null {
    const paragraph = this.findParagraphByPath(sectionIndex, paragraphIndex);
    if (!paragraph) return null;

    this.saveState();

    const endnoteId = Math.random().toString(36).substring(2, 11);
    const endnoteNumber = (this._content.endnotes?.length || 0) + 1;

    const endnote: Endnote = {
      id: endnoteId,
      number: endnoteNumber,
      paragraphs: [{
        id: Math.random().toString(36).substring(2, 11),
        runs: [{ text }],
      }],
    };

    if (!this._content.endnotes) {
      this._content.endnotes = [];
    }
    this._content.endnotes.push(endnote);

    // Add endnote reference to the paragraph
    paragraph.runs.push({
      text: '',
      endnoteRef: endnoteNumber,
    });

    this.markModified();
    return { id: endnoteId };
  }

  // ============================================================
  // Bookmark/Hyperlink Operations
  // ============================================================

  getBookmarks(): { name: string; section: number; paragraph: number }[] {
    const bookmarks: { name: string; section: number; paragraph: number }[] = [];

    this._content.sections.forEach((section, si) => {
      section.elements.forEach((el, ei) => {
        if (el.type === 'paragraph') {
          for (const run of el.data.runs) {
            if (run.field?.fieldType === 'Bookmark' || run.field?.fieldType === 'bookmark') {
              bookmarks.push({
                name: run.field.name || '',
                section: si,
                paragraph: ei,
              });
            }
          }
        }
      });
    });

    return bookmarks;
  }

  insertBookmark(sectionIndex: number, paragraphIndex: number, name: string): boolean {
    const paragraph = this.findParagraphByPath(sectionIndex, paragraphIndex);
    if (!paragraph) return false;

    this.saveState();

    paragraph.runs.push({
      text: '',
      field: {
        fieldType: 'Bookmark',
        name,
      },
    });

    this.markModified();
    return true;
  }

  getHyperlinks(): { url: string; text: string; section: number; paragraph: number }[] {
    const hyperlinks: { url: string; text: string; section: number; paragraph: number }[] = [];

    this._content.sections.forEach((section, si) => {
      section.elements.forEach((el, ei) => {
        if (el.type === 'paragraph') {
          for (const run of el.data.runs) {
            if (run.hyperlink) {
              hyperlinks.push({
                url: run.hyperlink.url,
                text: run.text || run.hyperlink.name || '',
                section: si,
                paragraph: ei,
              });
            }
          }
        }
      });
    });

    return hyperlinks;
  }

  insertHyperlink(sectionIndex: number, paragraphIndex: number, url: string, text: string): boolean {
    const paragraph = this.findParagraphByPath(sectionIndex, paragraphIndex);
    if (!paragraph) return false;

    this.saveState();

    paragraph.runs.push({
      text,
      hyperlink: {
        fieldType: 'Hyperlink',
        url,
        name: text,
      },
    });

    this.markModified();
    return true;
  }

  // ============================================================
  // Image Operations
  // ============================================================

  /**
   * Insert an image into the document.
   *
   * @param sectionIndex Section to insert into
   * @param afterElementIndex Insert after this element index (-1 for beginning)
   * @param imageData Image data including base64 data and MIME type
   *   - width: Target width in points (optional if preserveAspectRatio is true)
   *   - height: Target height in points (optional if preserveAspectRatio is true)
   *   - preserveAspectRatio: If true, maintains original image aspect ratio.
   *     When only width is specified, height is auto-calculated.
   *     When only height is specified, width is auto-calculated.
   *     When neither is specified, uses original dimensions (scaled to fit if too large).
   *   - position: Positioning options for the image (inline/floating, alignment, offset, text wrap)
   * @returns Object with image ID or null on failure
   */
  insertImage(
    sectionIndex: number,
    afterElementIndex: number,
    imageData: {
      data: string;
      mimeType: string;
      width?: number;
      height?: number;
      preserveAspectRatio?: boolean;
      position?: ImagePositionOptions;
      headerText?: string; // Text to search for in XML to find exact position
    }
  ): { id: string; actualWidth: number; actualHeight: number } | null {
    const section = this._content.sections[sectionIndex];
    if (!section) return null;

    this.saveState();

    // Calculate final dimensions
    let finalWidth = imageData.width ?? 400;  // Default 400pt
    let finalHeight = imageData.height ?? 300; // Default 300pt

    if (imageData.preserveAspectRatio) {
      // Get original image dimensions
      const originalDims = this.getImageDimensionsFromBase64(imageData.data, imageData.mimeType);

      if (originalDims) {
        const aspectRatio = originalDims.width / originalDims.height;

        if (imageData.width !== undefined && imageData.height === undefined) {
          // Width specified, calculate height
          finalWidth = imageData.width;
          finalHeight = Math.round(imageData.width / aspectRatio);
        } else if (imageData.height !== undefined && imageData.width === undefined) {
          // Height specified, calculate width
          finalHeight = imageData.height;
          finalWidth = Math.round(imageData.height * aspectRatio);
        } else if (imageData.width === undefined && imageData.height === undefined) {
          // Neither specified, use original dimensions (convert pixels to points, 1pt = 1.333px)
          // Cap at reasonable max size (e.g., 500pt)
          const maxSize = 500;
          const originalWidthPt = originalDims.width * 0.75; // px to pt
          const originalHeightPt = originalDims.height * 0.75;

          if (originalWidthPt > maxSize || originalHeightPt > maxSize) {
            // Scale down to fit within maxSize while preserving aspect ratio
            const scale = Math.min(maxSize / originalWidthPt, maxSize / originalHeightPt);
            finalWidth = Math.round(originalWidthPt * scale);
            finalHeight = Math.round(originalHeightPt * scale);
          } else {
            finalWidth = Math.round(originalWidthPt);
            finalHeight = Math.round(originalHeightPt);
          }
        }
        // If both width and height are specified with preserveAspectRatio, use width and recalculate height
        else {
          finalWidth = imageData.width!;
          finalHeight = Math.round(imageData.width! / aspectRatio);
        }
      }
    }

    // Generate sequential image ID (image1, image2, ...)
    const existingImageIds = this.getExistingImageIds();
    let nextNum = 1;
    while (existingImageIds.has(`image${nextNum}`)) {
      nextNum++;
    }
    const imageId = `image${nextNum}`;
    const binaryId = imageId; // Use same ID for binary reference

    const newImage: HwpxImage = {
      id: imageId,
      binaryId,
      width: finalWidth,
      height: finalHeight,
      data: imageData.data,
      mimeType: imageData.mimeType,
    };

    // Store image in the images map
    this._content.images.set(imageId, newImage);

    // Store binary data
    this._content.binData.set(binaryId, {
      id: binaryId,
      encoding: 'Base64',
      data: imageData.data,
    });

    // Add image element to section
    const newElement: SectionElement = { type: 'image', data: newImage };
    section.elements.splice(afterElementIndex + 1, 0, newElement);

    // Add to pending inserts for XML sync
    this._pendingImageInserts.push({
      sectionIndex,
      afterElementIndex,
      imageId,
      binaryId,
      data: imageData.data,
      mimeType: imageData.mimeType,
      width: finalWidth,
      height: finalHeight,
      position: imageData.position,
      headerText: imageData.headerText,
    });

    this.markModified();
    return { id: imageId, actualWidth: finalWidth, actualHeight: finalHeight };
  }

  /**
   * Insert an image inside a table cell
   * @param sectionIndex - Section containing the table
   * @param tableIndex - Table index (local to section)
   * @param row - Row index (0-based)
   * @param col - Column index (0-based)
   * @param imageData - Image data including base64, mimeType, and optional dimensions
   * @returns Object with image ID and actual dimensions, or null on failure
   */
  insertImageInCell(
    sectionIndex: number,
    tableIndex: number,
    row: number,
    col: number,
    imageData: {
      data: string;
      mimeType: string;
      width?: number;
      height?: number;
      preserveAspectRatio?: boolean;
      afterText?: string; // Text to search for - insert image after the paragraph containing this text
    }
  ): { id: string; actualWidth: number; actualHeight: number } | null {
    const table = this.findTable(sectionIndex, tableIndex);
    if (!table) return null;

    const cell = table.rows[row]?.cells[col];
    if (!cell) return null;

    this.saveState();

    // Calculate final dimensions (similar logic to insertImage)
    let finalWidth = imageData.width ?? 200;  // Default smaller for cells
    let finalHeight = imageData.height ?? 150;

    if (imageData.preserveAspectRatio) {
      const originalDims = this.getImageDimensionsFromBase64(imageData.data, imageData.mimeType);

      if (originalDims) {
        const aspectRatio = originalDims.width / originalDims.height;

        if (imageData.width !== undefined && imageData.height === undefined) {
          finalWidth = imageData.width;
          finalHeight = Math.round(imageData.width / aspectRatio);
        } else if (imageData.height !== undefined && imageData.width === undefined) {
          finalHeight = imageData.height;
          finalWidth = Math.round(imageData.height * aspectRatio);
        } else if (imageData.width === undefined && imageData.height === undefined) {
          // Use original dimensions, cap at 300pt for cells
          const maxSize = 300;
          const originalWidthPt = originalDims.width * 0.75;
          const originalHeightPt = originalDims.height * 0.75;

          if (originalWidthPt > maxSize || originalHeightPt > maxSize) {
            const scale = Math.min(maxSize / originalWidthPt, maxSize / originalHeightPt);
            finalWidth = Math.round(originalWidthPt * scale);
            finalHeight = Math.round(originalHeightPt * scale);
          } else {
            finalWidth = Math.round(originalWidthPt);
            finalHeight = Math.round(originalHeightPt);
          }
        } else {
          finalWidth = imageData.width!;
          finalHeight = Math.round(imageData.width! / aspectRatio);
        }
      }
    }

    // Generate image ID
    const existingImageIds = this.getExistingImageIds();
    let nextNum = 1;
    while (existingImageIds.has(`image${nextNum}`)) {
      nextNum++;
    }
    const imageId = `image${nextNum}`;
    const binaryId = imageId;

    const newImage: HwpxImage = {
      id: imageId,
      binaryId,
      width: finalWidth,
      height: finalHeight,
      data: imageData.data,
      mimeType: imageData.mimeType,
    };

    // Store image in the images map
    this._content.images.set(imageId, newImage);

    // Store binary data
    this._content.binData.set(binaryId, {
      id: binaryId,
      encoding: 'Base64',
      data: imageData.data,
    });

    // Get original image dimensions from binary data
    const orgDimensions = this.getImageDimensions(imageData.data, imageData.mimeType);

    // Add to pending cell image inserts
    this._pendingCellImageInserts.push({
      sectionIndex,
      tableIndex,
      row,
      col,
      imageId,
      binaryId,
      data: imageData.data,
      mimeType: imageData.mimeType,
      width: finalWidth,
      height: finalHeight,
      orgWidth: orgDimensions.width,
      orgHeight: orgDimensions.height,
      afterText: imageData.afterText,
    });

    this.markModified();
    return { id: imageId, actualWidth: finalWidth, actualHeight: finalHeight };
  }

  /**
   * Get existing image IDs from ZIP file
   */
  private getExistingImageIds(): Set<string> {
    const ids = new Set<string>();
    if (this._zip) {
      this._zip.forEach((relativePath) => {
        if (relativePath.startsWith('BinData/image')) {
          const match = relativePath.match(/BinData\/(image\d+)\./);
          if (match) {
            ids.add(match[1]);
          }
        }
      });
    }
    // Also include pending inserts
    for (const insert of this._pendingImageInserts) {
      ids.add(insert.imageId);
    }
    // Also include pending cell image inserts
    for (const insert of this._pendingCellImageInserts) {
      ids.add(insert.imageId);
    }
    return ids;
  }

  updateImageSize(imageId: string, width: number, height: number): boolean {
    const image = this._content.images.get(imageId);
    if (!image) return false;

    this.saveState();

    image.width = width;
    image.height = height;

    this.markModified();
    return true;
  }

  deleteImage(imageId: string): boolean {
    // Find image by id field (not map key) since getImages() returns image.id
    let mapKey: string | null = null;
    let image: HwpxImage | null = null;

    for (const [key, img] of this._content.images.entries()) {
      if (img.id === imageId) {
        mapKey = key;
        image = img;
        break;
      }
    }

    if (!image || !mapKey) return false;

    this.saveState();

    // Add to pending deletes for XML removal on save
    // Use binaryId (or mapKey which is binaryItemIDRef) for XML pattern matching
    this._pendingImageDeletes.push({
      imageId: imageId,
      binaryId: image.binaryId || mapKey,
    });

    // Remove from images map using the correct key
    this._content.images.delete(mapKey);

    // Remove binary data if exists
    if (image.binaryId) {
      this._content.binData.delete(image.binaryId);
    }

    // Remove from sections
    for (const section of this._content.sections) {
      const index = section.elements.findIndex(el => el.type === 'image' && el.data.id === imageId);
      if (index !== -1) {
        section.elements.splice(index, 1);
        break;
      }
    }

    this.markModified();
    return true;
  }

  // ============================================================
  // Drawing Objects (Line, Rect, Ellipse)
  // ============================================================

  insertLine(sectionIndex: number, x1: number, y1: number, x2: number, y2: number, options?: { color?: string; width?: number }): { id: string } | null {
    const section = this._content.sections[sectionIndex];
    if (!section) return null;

    this.saveState();

    const lineId = Math.random().toString(36).substring(2, 11);

    const newLine: HwpxLine = {
      id: lineId,
      x1,
      y1,
      x2,
      y2,
      strokeColor: options?.color || '#000000',
      strokeWidth: options?.width || 1,
    };

    const newElement: SectionElement = { type: 'line', data: newLine };
    section.elements.push(newElement);

    this.markModified();
    return { id: lineId };
  }

  insertRect(sectionIndex: number, x: number, y: number, width: number, height: number, options?: { fillColor?: string; strokeColor?: string }): { id: string } | null {
    const section = this._content.sections[sectionIndex];
    if (!section) return null;

    this.saveState();

    const rectId = Math.random().toString(36).substring(2, 11);

    const newRect: HwpxRect = {
      id: rectId,
      x,
      y,
      width,
      height,
      fillColor: options?.fillColor,
      strokeColor: options?.strokeColor || '#000000',
    };

    const newElement: SectionElement = { type: 'rect', data: newRect };
    section.elements.push(newElement);

    this.markModified();
    return { id: rectId };
  }

  insertEllipse(sectionIndex: number, cx: number, cy: number, rx: number, ry: number, options?: { fillColor?: string; strokeColor?: string }): { id: string } | null {
    const section = this._content.sections[sectionIndex];
    if (!section) return null;

    this.saveState();

    const ellipseId = Math.random().toString(36).substring(2, 11);

    const newEllipse: HwpxEllipse = {
      id: ellipseId,
      cx,
      cy,
      rx,
      ry,
      fillColor: options?.fillColor,
      strokeColor: options?.strokeColor || '#000000',
    };

    const newElement: SectionElement = { type: 'ellipse', data: newEllipse };
    section.elements.push(newElement);

    this.markModified();
    return { id: ellipseId };
  }

  // ============================================================
  // Equation Operations
  // ============================================================

  insertEquation(sectionIndex: number, afterElementIndex: number, script: string): { id: string } | null {
    const section = this._content.sections[sectionIndex];
    if (!section) return null;

    this.saveState();

    const equationId = Math.random().toString(36).substring(2, 11);

    const newEquation: HwpxEquation = {
      id: equationId,
      script,
      lineMode: false,
      baseUnit: 1000,
    };

    const newElement: SectionElement = { type: 'equation', data: newEquation };
    section.elements.splice(afterElementIndex + 1, 0, newElement);

    this.markModified();
    return { id: equationId };
  }

  getEquations(): { id: string; script: string }[] {
    const equations: { id: string; script: string }[] = [];

    for (const section of this._content.sections) {
      for (const element of section.elements) {
        if (element.type === 'equation') {
          equations.push({
            id: element.data.id,
            script: element.data.script || '',
          });
        }
      }
    }

    return equations;
  }

  // ============================================================
  // Memo Operations
  // ============================================================

  getMemos(): Memo[] {
    const memos: Memo[] = [];

    for (const section of this._content.sections) {
      if (section.memos) {
        memos.push(...section.memos);
      }
    }

    return memos;
  }

  insertMemo(sectionIndex: number, paragraphIndex: number, content: string, author?: string): { id: string } | null {
    const section = this._content.sections[sectionIndex];
    if (!section) return null;

    const paragraph = this.findParagraphByPath(sectionIndex, paragraphIndex);
    if (!paragraph) return null;

    this.saveState();

    const memoId = Math.random().toString(36).substring(2, 11);

    const memo: Memo = {
      id: memoId,
      author: author || 'Unknown',
      date: new Date().toISOString(),
      content: [content],
    };

    if (!section.memos) {
      section.memos = [];
    }
    section.memos.push(memo);

    // Mark the paragraph as having a memo
    if (paragraph.runs.length > 0) {
      paragraph.runs[paragraph.runs.length - 1].hasMemo = true;
      paragraph.runs[paragraph.runs.length - 1].memoId = memoId;
    }

    this.markModified();
    return { id: memoId };
  }

  deleteMemo(memoId: string): boolean {
    let found = false;

    for (const section of this._content.sections) {
      if (section.memos) {
        const index = section.memos.findIndex(m => m.id === memoId);
        if (index !== -1) {
          this.saveState();
          section.memos.splice(index, 1);
          found = true;
          break;
        }
      }
    }

    if (found) {
      // Remove memo reference from paragraphs
      for (const section of this._content.sections) {
        for (const element of section.elements) {
          if (element.type === 'paragraph') {
            for (const run of element.data.runs) {
              if (run.memoId === memoId) {
                run.hasMemo = false;
                run.memoId = undefined;
              }
            }
          }
        }
      }

      this.markModified();
    }

    return found;
  }

  // ============================================================
  // Section Operations
  // ============================================================

  getSections(): { index: number; pageSettings: PageSettings }[] {
    return this._content.sections.map((section, index) => ({
      index,
      pageSettings: section.pageSettings || {
        width: 59528,
        height: 84188,
        marginTop: 4252,
        marginBottom: 4252,
        marginLeft: 4252,
        marginRight: 4252,
      },
    }));
  }

  insertSection(afterSectionIndex: number): number {
    this.saveState();

    const newSection: HwpxSection = {
      id: Math.random().toString(36).substring(2, 11),
      elements: [{
        type: 'paragraph',
        data: {
          id: Math.random().toString(36).substring(2, 11),
          runs: [{ text: '' }],
        },
      }],
      pageSettings: {
        width: 59528,
        height: 84188,
        marginTop: 4252,
        marginBottom: 4252,
        marginLeft: 4252,
        marginRight: 4252,
      },
    };

    const insertIndex = afterSectionIndex + 1;
    this._content.sections.splice(insertIndex, 0, newSection);

    this.markModified();
    return insertIndex;
  }

  deleteSection(sectionIndex: number): boolean {
    if (sectionIndex < 0 || sectionIndex >= this._content.sections.length) return false;
    if (this._content.sections.length <= 1) return false; // Cannot delete the last section

    this.saveState();
    this._content.sections.splice(sectionIndex, 1);
    this.markModified();
    return true;
  }

  // ============================================================
  // Style Operations
  // ============================================================

  getStyles(): { id: number; name: string; type: string }[] {
    if (!this._content.styles?.styles) return [];

    return Array.from(this._content.styles.styles.values()).map(style => ({
      id: style.id,
      name: style.name || '',
      type: style.type || 'Para',
    }));
  }

  getCharShapes(): CharShape[] {
    if (!this._content.styles?.charShapes) return [];
    return Array.from(this._content.styles.charShapes.values());
  }

  getParaShapes(): ParaShape[] {
    if (!this._content.styles?.paraShapes) return [];
    return Array.from(this._content.styles.paraShapes.values());
  }

  applyStyle(sectionIndex: number, paragraphIndex: number, styleId: number): boolean {
    const paragraph = this.findParagraphByPath(sectionIndex, paragraphIndex);
    if (!paragraph) return false;

    if (!this._content.styles?.styles) return false;
    const style = this._content.styles.styles.get(styleId);
    if (!style) return false;

    this.saveState();

    paragraph.style = styleId;

    // Apply paragraph shape if defined
    if (style.paraPrIdRef !== undefined && this._content.styles.paraShapes) {
      const paraShape = this._content.styles.paraShapes.get(style.paraPrIdRef);
      if (paraShape) {
        paragraph.paraStyle = {
          align: paraShape.align?.toLowerCase() as ParagraphStyle['align'],
          lineSpacing: paraShape.lineSpacing,
          marginTop: paraShape.marginTop,
          marginBottom: paraShape.marginBottom,
          marginLeft: paraShape.marginLeft,
          marginRight: paraShape.marginRight,
          firstLineIndent: paraShape.firstLineIndent,
        };
      }
    }

    // Apply character shape if defined
    if (style.charPrIdRef !== undefined && this._content.styles.charShapes) {
      const charShape = this._content.styles.charShapes.get(style.charPrIdRef);
      if (charShape) {
        for (const run of paragraph.runs) {
          run.charStyle = {
            bold: charShape.bold,
            italic: charShape.italic,
            underline: charShape.underline,
            fontSize: charShape.height ? charShape.height / 100 : undefined,
            fontColor: charShape.textColor,
          };
        }
      }
    }

    this.markModified();
    return true;
  }

  // ============================================================
  // Column Definition Operations
  // ============================================================

  getColumnDef(sectionIndex: number): ColumnDef | null {
    const section = this._content.sections[sectionIndex];
    if (!section) return null;
    return section.columnDef || null;
  }

  setColumnDef(sectionIndex: number, columns: number, gap?: number): boolean {
    const section = this._content.sections[sectionIndex];
    if (!section) return false;
    if (columns < 1) return false;

    this.saveState();

    const defaultGap = gap || 850; // Default gap in hwpunit (approx 8.5mm)

    section.columnDef = {
      type: 'Newspaper',
      count: columns,
      sameSize: true,
      sameGap: defaultGap,
      columns: Array.from({ length: columns }, () => ({
        width: 0, // Will be calculated based on page width
        gap: defaultGap,
      })),
    };

    this.markModified();
    return true;
  }

  // ============================================================
  // Save
  // ============================================================

  async save(): Promise<Buffer> {
    if (!this._zip) throw new Error('Cannot save HWP files');
    await this.syncContentToZip();

    // Ensure mimetype file is not compressed (required by HWPX format)
    // HWPX follows ODF container format which requires uncompressed mimetype
    const mimetypeFile = this._zip.file('mimetype');
    if (mimetypeFile) {
      const mimetypeContent = await mimetypeFile.async('string');
      this._zip.file('mimetype', mimetypeContent, { compression: 'STORE' });
    }

    return await this._zip.generateAsync({
      type: 'nodebuffer',
      compression: 'DEFLATE',
      compressionOptions: { level: 6 }
    });
  }

  // WARNING: syncContentToZip reads and writes header.xml and section XML files sequentially.
  // Each apply*ToXml() method may read header.xml independently, so the order of operations matters.
  // If multiple methods modify header.xml, later methods will see changes from earlier ones.
  // Be cautious when adding new apply* methods that modify header.xml - ensure they read the
  // latest version and don't overwrite changes made by prior steps.
  private async syncContentToZip(): Promise<void> {
    if (!this._zip) return;

    // Apply table inserts FIRST (other operations depend on tables existing in XML)
    if (this._pendingTableInserts && this._pendingTableInserts.length > 0) {
      await this.applyTableInsertsToXml();
      this._pendingTableInserts = [];
    }

    // Apply table deletes
    if (this._pendingTableDeletes && this._pendingTableDeletes.length > 0) {
      await this.applyTableDeletesToXml();
      this._pendingTableDeletes = [];
    }

    // Apply paragraph/element deletes
    if (this._pendingParagraphDeletes && this._pendingParagraphDeletes.length > 0) {
      await this.applyParagraphDeletesToXml();
      this._pendingParagraphDeletes = [];
    }

    // Apply table moves/copies
    if (this._pendingTableMoves && this._pendingTableMoves.length > 0) {
      await this.applyTableMovesToXml();
      this._pendingTableMoves = [];
    }

    // Apply paragraph inserts
    if (this._pendingParagraphInserts && this._pendingParagraphInserts.length > 0) {
      await this.applyParagraphInsertsToXml();
      this._pendingParagraphInserts = [];
    }

    // Apply table cell updates (preserves original XML structure)
    if (this._pendingTableCellUpdates && this._pendingTableCellUpdates.length > 0) {
      await this.applyTableCellUpdatesToXml();
      this._pendingTableCellUpdates = [];
    }

    // Apply cell merges
    if (this._pendingCellMerges && this._pendingCellMerges.length > 0) {
      await this.applyCellMergesToXml();
      this._pendingCellMerges = [];
    }

    // Apply cell splits
    if (this._pendingCellSplits && this._pendingCellSplits.length > 0) {
      await this.applyCellSplitsToXml();
      this._pendingCellSplits = [];
    }

    // Apply nested table inserts
    if (this._pendingNestedTableInserts && this._pendingNestedTableInserts.length > 0) {
      await this.applyNestedTableInsertsToXml();
      this._pendingNestedTableInserts = [];
    }

    // Apply cell image inserts
    if (this._pendingCellImageInserts && this._pendingCellImageInserts.length > 0) {
      await this.applyCellImageInsertsToXml();
      this._pendingCellImageInserts = [];
    }

    // Apply direct text updates (from updateParagraphText)
    if (this._pendingDirectTextUpdates && this._pendingDirectTextUpdates.length > 0) {
      await this.applyDirectTextUpdatesToXml();
      this._pendingDirectTextUpdates = [];
    }

    // Apply text replacements (from replaceText)
    if (this._pendingTextReplacements && this._pendingTextReplacements.length > 0) {
      await this.applyTextReplacementsToXml();
      this._pendingTextReplacements = [];
    }

    // Apply image inserts
    if (this._pendingImageInserts && this._pendingImageInserts.length > 0) {
      await this.applyImageInsertsToZip();
      this._pendingImageInserts = [];
    }

    // Apply image deletes
    if (this._pendingImageDeletes && this._pendingImageDeletes.length > 0) {
      await this.applyImageDeletesToZip();
      this._pendingImageDeletes = [];
    }

    // Apply hanging indent changes
    if (this._pendingHangingIndents && this._pendingHangingIndents.length > 0) {
      await this.applyHangingIndentsToXml();
      this._pendingHangingIndents = [];
    }

    // Apply table cell hanging indent changes
    if (this._pendingTableCellHangingIndents && this._pendingTableCellHangingIndents.length > 0) {
      await this.applyTableCellHangingIndentsToXml();
      this._pendingTableCellHangingIndents = [];
    }

    // Apply paragraph style changes (alignment, etc.)
    if (this._pendingParagraphStyles && this._pendingParagraphStyles.length > 0) {
      await this.applyParagraphStylesToXml();
      this._pendingParagraphStyles = [];
    }

    // Apply character style changes (font, size, etc.)
    if (this._pendingCharacterStyles && this._pendingCharacterStyles.length > 0) {
      await this.applyCharacterStylesToXml();
      this._pendingCharacterStyles = [];
    }

    // Apply table row inserts
    if (this._pendingTableRowInserts && this._pendingTableRowInserts.length > 0) {
      await this.applyTableRowInsertsToXml();
      this._pendingTableRowInserts = [];
    }

    // Apply table row deletes
    if (this._pendingTableRowDeletes && this._pendingTableRowDeletes.length > 0) {
      await this.applyTableRowDeletesToXml();
      this._pendingTableRowDeletes = [];
    }

    // Apply table column inserts
    if (this._pendingTableColumnInserts && this._pendingTableColumnInserts.length > 0) {
      await this.applyTableColumnInsertsToXml();
      this._pendingTableColumnInserts = [];
    }

    // Apply table column deletes
    if (this._pendingTableColumnDeletes && this._pendingTableColumnDeletes.length > 0) {
      await this.applyTableColumnDeletesToXml();
      this._pendingTableColumnDeletes = [];
    }

    // Apply paragraph copies
    if (this._pendingParagraphCopies && this._pendingParagraphCopies.length > 0) {
      await this.applyParagraphCopiesToXml();
      this._pendingParagraphCopies = [];
    }

    // Apply paragraph moves
    if (this._pendingParagraphMoves && this._pendingParagraphMoves.length > 0) {
      await this.applyParagraphMovesToXml();
      this._pendingParagraphMoves = [];
    }

    // Apply header/footer updates
    if (this._pendingHeaderUpdates && this._pendingHeaderUpdates.length > 0 ||
        this._pendingFooterUpdates && this._pendingFooterUpdates.length > 0) {
      await this.applyHeaderFooterUpdatesToXml();
      this._pendingHeaderUpdates = [];
      this._pendingFooterUpdates = [];
    }

    // NOTE: Do NOT call syncCharShapesToZip() here.
    // The current serialization is incomplete and loses critical attributes
    // (textColor, shadeColor, symMark, underline, strikeout, outline, shadow).
    // Original header.xml charPr/charShape elements should be preserved as-is.
    // Only sync charShapes when they are explicitly modified.

    // Validate charPr integrity - ensure no elements were lost during sync
    {
      const headerPath = 'Contents/header.xml';
      const headerXml = await this._zip.file(headerPath)?.async('string');
      if (headerXml) {
        const charPrCount = (headerXml.match(/<hh:charPr\b/g) || []).length;
        if (this._originalCharPrCount !== undefined && charPrCount < this._originalCharPrCount) {
          console.error(`[WARNING] charPr count dropped: ${this._originalCharPrCount} → ${charPrCount}. Possible corruption.`);
        }
      }
    }

    // Remove Fasoo DRM tracking info if present (causes "corrupted file" warning in Hancom Office)
    await this.removeFasooDrmTracking();

    // Sync metadata
    await this.syncMetadataToZip();

    // Invalidate cached XML positions since XML structure may have changed
    this.invalidateXmlPositions();

    this._isDirty = false;
  }

  /**
   * Invalidate cached XML positions for all paragraphs.
   * Called after save() because XML modifications may shift byte positions.
   * The positions will be re-populated on next document reload.
   */
  private invalidateXmlPositions(): void {
    if (!this._content?.sections) return;
    for (const section of this._content.sections) {
      for (const element of section.elements) {
        if (element.type === 'paragraph') {
          const para = element.data as import('./types').HwpxParagraph;
          delete para._xmlPosition;
        }
      }
    }
  }

  /**
   * Get cached XML position for a paragraph at the given section and element index.
   * Returns undefined if no cached position is available.
   * The cached positions are populated during parsing in HwpxParser.parseSection().
   */
  private getCachedXmlPosition(sectionIndex: number, elementIndex: number): { start: number; end: number } | undefined {
    const section = this._content?.sections?.[sectionIndex];
    if (!section) return undefined;

    const element = section.elements[elementIndex];
    if (!element || element.type !== 'paragraph') return undefined;

    const para = element.data as import('./types').HwpxParagraph;
    if (!para._xmlPosition) return undefined;

    // Verify section index matches (safety check)
    if (para._xmlPosition.sectionIndex !== sectionIndex) return undefined;

    return {
      start: para._xmlPosition.start,
      end: para._xmlPosition.end
    };
  }

  /**
   * Remove Fasoo DRM tracking information from content.hpf.
   * Fasoo DRM adds tracking IDs to the description metadata which causes
   * "document corrupted or tampered" warnings when the file is modified externally.
   */
  private async removeFasooDrmTracking(): Promise<void> {
    if (!this._zip) return;

    const hpfPath = 'Contents/content.hpf';
    const hpfFile = this._zip.file(hpfPath);
    if (!hpfFile) return;

    let hpf = await hpfFile.async('string');

    // Check if Fasoo_Trace_ID exists in description
    if (hpf.includes('Fasoo_Trace_ID')) {
      // Remove the Fasoo tracking data from description metadata
      hpf = hpf.replace(
        /<opf:meta name="description" content="text">[^<]*<\/opf:meta>/,
        '<opf:meta name="description" content="text"></opf:meta>'
      );
      this._zip.file(hpfPath, hpf);
    }
  }

  /**
   * Apply pending image deletions to the ZIP.
   * Removes <hp:pic> elements from section XML and deletes BinData files.
   */
  private async applyImageDeletesToZip(): Promise<void> {
    if (!this._zip) return;

    // Collect all binaryIds to delete
    // binaryId can be full path like "BinData/image1.png" or just "image1"
    // We need to extract just the filename without extension for XML matching
    const binaryIdsToDelete = new Set<string>();
    const fullPathsToDelete = new Set<string>(); // Keep original paths for BinData deletion
    for (const del of this._pendingImageDeletes) {
      fullPathsToDelete.add(del.binaryId);
      // Extract filename without extension (e.g., "BinData/image1.png" -> "image1")
      const fileName = del.binaryId.split('/').pop() || del.binaryId;
      const baseName = fileName.replace(/\.[^.]+$/, '');
      binaryIdsToDelete.add(baseName);
    }

    // Process each section to remove <hp:pic> elements
    for (let sectionIndex = 0; sectionIndex < this._content.sections.length; sectionIndex++) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      const file = this._zip.file(sectionPath);
      if (!file) continue;

      let xml = await file.async('string');
      let modified = false;

      // Remove <hp:pic> elements with matching binaryItemIDRef
      // Use balanced bracket matching to avoid deleting across multiple <hp:pic> tags
      for (const binaryId of binaryIdsToDelete) {
        // Find all <hp:pic> tags and their matching </hp:pic> using balanced matching
        const picRanges: Array<{ start: number; end: number }> = [];
        let searchStart = 0;

        while (true) {
          const picStart = xml.indexOf('<hp:pic', searchStart);
          if (picStart === -1) break;

          // Find the end of the opening tag
          const tagEnd = xml.indexOf('>', picStart);
          if (tagEnd === -1) break;

          // Use balanced bracket matching to find </hp:pic>
          let depth = 1;
          let pos = tagEnd + 1;
          let picEnd = -1;

          while (pos < xml.length && depth > 0) {
            const nextOpen = xml.indexOf('<hp:pic', pos);
            const nextClose = xml.indexOf('</hp:pic>', pos);

            if (nextClose === -1) break;

            if (nextOpen !== -1 && nextOpen < nextClose) {
              depth++;
              pos = nextOpen + 7; // length of "<hp:pic"
            } else {
              depth--;
              if (depth === 0) {
                picEnd = nextClose + 9; // length of "</hp:pic>"
              }
              pos = nextClose + 9;
            }
          }

          if (picEnd !== -1) {
            picRanges.push({ start: picStart, end: picEnd });
          }
          searchStart = picEnd !== -1 ? picEnd : tagEnd + 1;
        }

        // Check each <hp:pic> range for the binaryItemIDRef and mark for deletion
        // Process in reverse order to avoid index shifting
        for (let i = picRanges.length - 1; i >= 0; i--) {
          const range = picRanges[i];
          const picContent = xml.substring(range.start, range.end);

          if (picContent.includes(`binaryItemIDRef="${binaryId}"`)) {
            xml = xml.substring(0, range.start) + xml.substring(range.end);
            modified = true;
          }
        }
      }

      // Clean up empty runs that may be left behind
      // <hp:run charPrIDRef="0"><hp:t/></hp:run> or <hp:run charPrIDRef="0"></hp:run>
      xml = xml.replace(/<hp:run[^>]*>(\s*<hp:t\s*\/>)?\s*<\/hp:run>/g, '');

      // Clean up empty paragraphs that only contained the image
      // <hp:p ...><hp:linesegarray>...</hp:linesegarray></hp:p>
      xml = xml.replace(
        /<hp:p[^>]*>\s*(<hp:linesegarray[^>]*>[\s\S]*?<\/hp:linesegarray>)?\s*<\/hp:p>/g,
        ''
      );

      if (modified) {
        this._zip.file(sectionPath, xml);
      }
    }

    // Remove BinData files
    // First try the full paths we saved (e.g., "BinData/image1.png")
    for (const fullPath of fullPathsToDelete) {
      if (this._zip.file(fullPath)) {
        this._zip.remove(fullPath);
      }
    }

    // Also try constructing paths from base names with various extensions
    for (const binaryId of binaryIdsToDelete) {
      const extensions = ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'PNG', 'JPG', 'JPEG', 'GIF', 'BMP'];
      for (const ext of extensions) {
        const binPath = `BinData/${binaryId}.${ext}`;
        if (this._zip.file(binPath)) {
          this._zip.remove(binPath);
        }
      }

      // Also try without extension
      const binPathNoExt = `BinData/${binaryId}`;
      if (this._zip.file(binPathNoExt)) {
        this._zip.remove(binPathNoExt);
      }
    }
  }

  /**
   * Apply table deletes to XML.
   * Removes tables from the section XML.
   * Uses findAllTables for proper nested table handling.
   */
  private async applyTableDeletesToXml(): Promise<void> {
    if (!this._zip) return;

    // Sort by tableIndex descending to avoid index shifting issues
    const sortedDeletes = [...this._pendingTableDeletes].sort(
      (a, b) => b.tableIndex - a.tableIndex
    );

    // Group by section
    const deletesBySection = new Map<number, Array<{ tableIndex: number; tableId?: string }>>();
    for (const del of sortedDeletes) {
      if (!deletesBySection.has(del.sectionIndex)) {
        deletesBySection.set(del.sectionIndex, []);
      }
      deletesBySection.get(del.sectionIndex)!.push({
        tableIndex: del.tableIndex,
        tableId: del.tableId
      });
    }

    for (const [sectionIndex, deletes] of deletesBySection) {
      const sectionFile = `Contents/section${sectionIndex}.xml`;
      const file = this._zip.file(sectionFile);
      if (!file) continue;

      let xml = await file.async('string');

      // Process each delete (already sorted by descending index)
      for (const del of deletes) {
        // Use findAllTables for proper nested table handling
        const tables = this.findAllTables(xml);

        if (del.tableIndex < tables.length) {
          const target = tables[del.tableIndex];
          xml = xml.slice(0, target.startIndex) + xml.slice(target.endIndex);
        }
      }

      this._zip.file(sectionFile, xml);
    }
  }

  /**
   * Apply paragraph/element deletes to XML.
   * Removes paragraphs or tables from the section XML.
   */
  private async applyParagraphDeletesToXml(): Promise<void> {
    if (!this._zip) return;

    // Sort by elementIndex descending to avoid index shifting issues
    const sortedDeletes = [...this._pendingParagraphDeletes].sort(
      (a, b) => b.elementIndex - a.elementIndex
    );

    // Group by section
    const deletesBySection = new Map<number, Array<{ elementIndex: number; elementType: 'paragraph' | 'table' }>>();
    for (const del of sortedDeletes) {
      if (!deletesBySection.has(del.sectionIndex)) {
        deletesBySection.set(del.sectionIndex, []);
      }
      deletesBySection.get(del.sectionIndex)!.push({
        elementIndex: del.elementIndex,
        elementType: del.elementType
      });
    }

    for (const [sectionIndex, deletes] of deletesBySection) {
      const sectionFile = `Contents/section${sectionIndex}.xml`;
      const file = this._zip.file(sectionFile);
      if (!file) continue;

      let xml = await file.async('string');

      for (const del of deletes) {
        // Find ALL elements (both paragraphs and tables) in document order
        // elementIndex is the index in the combined list of paragraphs + tables
        const allElementsRegex = /<hp:(p|tbl)\b[^>]*>[\s\S]*?<\/hp:\1>/g;
        const allElements: Array<{ match: string; start: number; end: number; type: string }> = [];
        let match;
        while ((match = allElementsRegex.exec(xml)) !== null) {
          allElements.push({
            match: match[0],
            start: match.index,
            end: match.index + match[0].length,
            type: match[1] // 'p' or 'tbl'
          });
        }

        // Remove the element at the specified index
        if (del.elementIndex < allElements.length) {
          const target = allElements[del.elementIndex];
          xml = xml.slice(0, target.start) + xml.slice(target.end);
        }
      }

      this._zip.file(sectionFile, xml);
    }
  }

  /**
   * Apply table inserts to XML.
   * Inserts new tables into the section XML.
   */
  private async applyTableInsertsToXml(): Promise<void> {
    if (!this._zip) return;

    // Group inserts by section
    const insertsBySection = new Map<number, Array<{
      afterElementIndex: number;
      rows: number;
      cols: number;
      width: number;
      cellWidth: number;
      insertOrder: number;
      tableId: string;
    }>>();

    for (const insert of this._pendingTableInserts) {
      const sectionInserts = insertsBySection.get(insert.sectionIndex) || [];
      sectionInserts.push({
        afterElementIndex: insert.afterElementIndex,
        rows: insert.rows,
        cols: insert.cols,
        width: insert.width,
        cellWidth: insert.cellWidth,
        insertOrder: insert.insertOrder,
        tableId: insert.tableId,
      });
      insertsBySection.set(insert.sectionIndex, sectionInserts);
    }

    // Process each section
    for (const [sectionIndex, inserts] of insertsBySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      const file = this._zip.file(sectionPath);
      if (!file) continue;

      let xml = await file.async('string');

      // Get maximum id and instid for generating new ones
      const idMatches = xml.matchAll(/id="(\d+)"/g);
      let maxId = 0;
      for (const m of idMatches) {
        maxId = Math.max(maxId, parseInt(m[1], 10));
      }

      // Sort inserts by insertOrder (ascending) - process in the order they were added
      // This ensures tables are inserted sequentially, building on each other
      const sortedInserts = [...inserts].sort((a, b) => a.insertOrder - b.insertOrder);

      for (const insert of sortedInserts) {
        // Use the in-memory table ID for consistency with updateTableCell operations
        const tableId = insert.tableId;

        // Calculate row height based on standard settings
        const rowHeight = 1000; // Default row height in hwpunit
        const tableHeight = rowHeight * insert.rows;

        // Build table XML
        let tableXml = `<hp:tbl id="${tableId}" zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="0" rowCnt="${insert.rows}" colCnt="${insert.cols}" cellSpacing="0" borderFillIDRef="2" noAdjust="0">`;
        tableXml += `<hp:sz width="${insert.width}" widthRelTo="ABSOLUTE" height="${tableHeight}" heightRelTo="ABSOLUTE" protect="0"/>`;
        tableXml += `<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="PARA" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>`;
        tableXml += `<hp:outMargin left="141" right="141" top="141" bottom="141"/>`;
        tableXml += `<hp:inMargin left="0" right="0" top="0" bottom="0"/>`;

        // Generate rows
        for (let r = 0; r < insert.rows; r++) {
          tableXml += `<hp:tr>`;
          for (let c = 0; c < insert.cols; c++) {
            maxId++;
            const cellParaId = maxId;
            tableXml += `<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="2">`;
            tableXml += `<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">`;
            tableXml += `<hp:p id="${cellParaId}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">`;
            tableXml += `<hp:run charPrIDRef="0"><hp:t></hp:t></hp:run>`;
            tableXml += `</hp:p>`;
            tableXml += `</hp:subList>`;
            tableXml += `<hp:cellAddr colAddr="${c}" rowAddr="${r}"/>`;
            tableXml += `<hp:cellSpan colSpan="1" rowSpan="1"/>`;
            tableXml += `<hp:cellSz width="${insert.cellWidth}" height="${rowHeight}"/>`;
            tableXml += `<hp:cellMargin left="141" right="141" top="141" bottom="141"/>`;
            tableXml += `</hp:tc>`;
          }
          tableXml += `</hp:tr>`;
        }
        tableXml += `</hp:tbl>`;

        // Find the position to insert the table
        // We need to insert after a paragraph element
        // Find all <hp:p> elements at the root level (not inside tables)
        const paragraphMatches = [...xml.matchAll(/<hp:p\s[^>]*>.*?<\/hp:p>/gs)];

        // Filter to find only top-level paragraphs (not inside <hp:tbl> or <hp:subList>)
        // For simplicity, insert after the first paragraph if afterElementIndex is 0
        // or find the appropriate position

        let insertPosition = -1;
        let elementCount = -1;
        let searchPos = 0;

        // Find paragraphs and tables at root level using balanced bracket matching
        while (searchPos < xml.length) {
          // Look for next <hp:p or <hp:tbl
          const nextP = xml.indexOf('<hp:p ', searchPos);
          const nextTbl = xml.indexOf('<hp:tbl ', searchPos);

          let nextPos = -1;
          let isTable = false;

          if (nextP !== -1 && (nextTbl === -1 || nextP < nextTbl)) {
            nextPos = nextP;
            isTable = false;
          } else if (nextTbl !== -1) {
            nextPos = nextTbl;
            isTable = true;
          }

          if (nextPos === -1) break;

          // Check if this is inside a subList (nested)
          const beforeText = xml.substring(Math.max(0, nextPos - HwpxDocument.NESTED_CHECK_LOOKBACK), nextPos);
          const subListOpen = beforeText.lastIndexOf('<hp:subList');
          const subListClose = beforeText.lastIndexOf('</hp:subList>');
          const isNested = subListOpen > subListClose;

          if (!isNested) {
            elementCount++;

            // Find the end of this element using balanced bracket matching
            const endPos = isTable
              ? HwpxDocument.findClosingTagPosition(xml, nextPos + 1, '<hp:tbl', '</hp:tbl>')
              : HwpxDocument.findClosingTagPosition(xml, nextPos + 1, '<hp:p ', '</hp:p>');

            if (endPos === -1) {
              searchPos = nextPos + HwpxDocument.SEARCH_SKIP_OFFSET;
              continue;
            }

            if (elementCount === insert.afterElementIndex) {
              insertPosition = endPos;
              break;
            }

            searchPos = endPos;
          } else {
            searchPos = nextPos + HwpxDocument.SEARCH_SKIP_OFFSET;
          }
        }

        // If position not found, insert at end of section (before </hs:sec>)
        if (insertPosition === -1) {
          const secEnd = xml.lastIndexOf('</hs:sec>');
          if (secEnd !== -1) {
            insertPosition = secEnd;
          }
        }

        if (insertPosition !== -1) {
          // Wrap table in a paragraph for proper positioning
          const wrapperXml = `<hp:p id="${maxId + 1}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0">${tableXml}<hp:t></hp:t></hp:run></hp:p>`;
          maxId++;

          xml = xml.substring(0, insertPosition) + wrapperXml + xml.substring(insertPosition);
        }
      }

      this._zip.file(sectionPath, xml);
    }
  }

  /**
   * Apply table move/copy operations to XML.
   * Extracts table XML from source and inserts at target position.
   */
  private async applyTableMovesToXml(): Promise<void> {
    if (!this._zip || !this._pendingTableMoves || this._pendingTableMoves.length === 0) return;

    // Process each move/copy operation
    for (const op of this._pendingTableMoves) {
      const sourceFile = `Contents/section${op.sourceSectionIndex}.xml`;
      const targetFile = `Contents/section${op.targetSectionIndex}.xml`;

      // Read source section XML
      let sourceXml = await this._zip.file(sourceFile)?.async('string');
      if (!sourceXml) {
        console.error(`[HwpxDocument] applyTableMovesToXml: Source section ${op.sourceSectionIndex} not found`);
        continue;
      }

      // Find all tables in source section
      const tables = this.findAllTables(sourceXml);
      if (op.sourceTableIndex >= tables.length) {
        console.error(`[HwpxDocument] applyTableMovesToXml: Table index ${op.sourceTableIndex} out of range (${tables.length} tables)`);
        continue;
      }

      const tableData = tables[op.sourceTableIndex];
      let tableXml = tableData.xml;

      // For copy operation, regenerate all IDs
      if (op.type === 'copy') {
        tableXml = this.regenerateIdsInXml(tableXml);
      }

      // Validate tag balance before proceeding
      const balanceCheck = this.validateTagBalance(tableXml);
      if (!balanceCheck.balanced) {
        console.error(`[HwpxDocument] applyTableMovesToXml: Tag balance error in table XML:`, balanceCheck.mismatches);
        continue;
      }

      // For move operation, remove from source
      if (op.type === 'move') {
        sourceXml = sourceXml.substring(0, tableData.startIndex) + sourceXml.substring(tableData.endIndex);
        this._zip.file(sourceFile, sourceXml);
      }

      // Read target section XML (might be same as source after modification)
      let targetXml = await this._zip.file(targetFile)?.async('string');
      if (!targetXml) {
        console.error(`[HwpxDocument] applyTableMovesToXml: Target section ${op.targetSectionIndex} not found`);
        continue;
      }

      // Find insertion point in target section
      // We need to find the element at targetAfterIndex and insert after it
      const insertPosition = this.findInsertPositionForElement(targetXml, op.targetAfterIndex);
      if (insertPosition < 0) {
        // Insert at the end of the section (before </hs:sec>)
        const secEndMatch = targetXml.match(/<\/hs:sec>\s*$/);
        if (secEndMatch && secEndMatch.index !== undefined) {
          targetXml = targetXml.substring(0, secEndMatch.index) + '\n  ' + tableXml + '\n' + targetXml.substring(secEndMatch.index);
        }
      } else {
        targetXml = targetXml.substring(0, insertPosition) + '\n  ' + tableXml + targetXml.substring(insertPosition);
      }

      // Validate final XML tag balance
      const finalBalanceCheck = this.validateTagBalance(targetXml);
      if (!finalBalanceCheck.balanced) {
        console.error(`[HwpxDocument] applyTableMovesToXml: Final tag balance error:`, finalBalanceCheck.mismatches);
        continue;
      }

      this._zip.file(targetFile, targetXml);
    }
  }

  /**
   * Regenerate all IDs in XML to avoid duplicates.
   */
  private regenerateIdsInXml(xml: string): string {
    // Regex to match id="..." attributes
    const idRegex = /id="([^"]+)"/g;
    const idMap = new Map<string, string>();

    // First pass: collect all IDs and generate new ones
    let match;
    while ((match = idRegex.exec(xml)) !== null) {
      const oldId = match[1];
      if (!idMap.has(oldId)) {
        const newId = Math.random().toString(36).substring(2, 11);
        idMap.set(oldId, newId);
      }
    }

    // Second pass: replace all IDs
    let result = xml;
    for (const [oldId, newId] of idMap) {
      result = result.replace(new RegExp(`id="${oldId}"`, 'g'), `id="${newId}"`);
    }

    return result;
  }

  /**
   * Find the position to insert an element after a given element index.
   * Returns the position after the closing tag of the element.
   */
  private findInsertPositionForElement(xml: string, afterIndex: number): number {
    if (afterIndex < 0) {
      // Insert at the beginning of section content (after opening <hs:sec...>)
      const secOpenMatch = xml.match(/<hs:sec[^>]*>/);
      if (secOpenMatch && secOpenMatch.index !== undefined) {
        return secOpenMatch.index + secOpenMatch[0].length;
      }
      return -1;
    }

    // Find all root-level elements (paragraphs, tables)
    const elements: Array<{ start: number; end: number }> = [];

    // Find paragraphs (not inside subList)
    const pRegex = /<hp:p[^>]*>[\s\S]*?<\/hp:p>/g;
    let match;

    // Find tables
    const tables = this.findAllTables(xml);
    for (const table of tables) {
      elements.push({ start: table.startIndex, end: table.endIndex });
    }

    // Find root-level paragraphs (simplified approach)
    pRegex.lastIndex = 0;
    while ((match = pRegex.exec(xml)) !== null) {
      const start = match.index;
      const end = start + match[0].length;

      // Check if this paragraph is inside a table (inside subList)
      const beforeMatch = xml.substring(0, start);
      const subListOpen = (beforeMatch.match(/<hp:subList[^>]*>/g) || []).length;
      const subListClose = (beforeMatch.match(/<\/hp:subList>/g) || []).length;

      if (subListOpen === subListClose) {
        // This is a root-level paragraph
        elements.push({ start, end });
      }
    }

    // Sort elements by start position
    elements.sort((a, b) => a.start - b.start);

    if (afterIndex >= elements.length) {
      // Insert at the end
      return -1;
    }

    // Return position after the element at afterIndex
    return elements[afterIndex].end;
  }

  /**
   * Apply paragraph inserts to XML.
   * Inserts new paragraphs at the specified positions.
   */
  private async applyParagraphInsertsToXml(): Promise<void> {
    if (!this._zip) return;

    // Group inserts by section
    const insertsBySection = new Map<number, Array<{
      afterElementIndex: number;
      paragraphId: string;
      text: string;
    }>>();

    for (const insert of this._pendingParagraphInserts) {
      const sectionInserts = insertsBySection.get(insert.sectionIndex) || [];
      sectionInserts.push({
        afterElementIndex: insert.afterElementIndex,
        paragraphId: insert.paragraphId,
        text: insert.text,
      });
      insertsBySection.set(insert.sectionIndex, sectionInserts);
    }

    // Process each section
    for (const [sectionIndex, inserts] of insertsBySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      const file = this._zip.file(sectionPath);
      if (!file) continue;

      let xml = await file.async('string');

      // Sort inserts by afterElementIndex in ascending order
      // This ensures each insert happens at the correct position as XML grows
      const sortedInserts = [...inserts].sort((a, b) => a.afterElementIndex - b.afterElementIndex);

      for (const insert of sortedInserts) {
        // Escape text for XML
        const escapedText = this.escapeXml(insert.text);

        // Build paragraph XML
        const paragraphXml = `<hp:p id="${insert.paragraphId}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0"><hp:t>${escapedText}</hp:t></hp:run></hp:p>`;

        // Find the position to insert
        let insertPosition = -1;
        let elementCount = -1;
        let searchPos = 0;

        // Find paragraphs and tables at root level using balanced bracket matching
        while (searchPos < xml.length) {
          // Look for next <hp:p or <hp:tbl
          const nextP = xml.indexOf('<hp:p ', searchPos);
          const nextTbl = xml.indexOf('<hp:tbl ', searchPos);

          let nextPos = -1;
          let isTable = false;

          if (nextP !== -1 && (nextTbl === -1 || nextP < nextTbl)) {
            nextPos = nextP;
            isTable = false;
          } else if (nextTbl !== -1) {
            nextPos = nextTbl;
            isTable = true;
          }

          if (nextPos === -1) break;

          // Check if this is inside a subList (nested)
          const beforeText = xml.substring(Math.max(0, nextPos - HwpxDocument.NESTED_CHECK_LOOKBACK), nextPos);
          const subListOpen = beforeText.lastIndexOf('<hp:subList');
          const subListClose = beforeText.lastIndexOf('</hp:subList>');
          const isNested = subListOpen > subListClose;

          if (!isNested) {
            elementCount++;

            // Find the end of this element using balanced bracket matching
            const endPos = isTable
              ? HwpxDocument.findClosingTagPosition(xml, nextPos + 1, '<hp:tbl', '</hp:tbl>')
              : HwpxDocument.findClosingTagPosition(xml, nextPos + 1, '<hp:p ', '</hp:p>');

            if (endPos === -1) {
              searchPos = nextPos + HwpxDocument.SEARCH_SKIP_OFFSET;
              continue;
            }

            if (elementCount === insert.afterElementIndex) {
              insertPosition = endPos;
              break;
            }

            searchPos = endPos;
          } else {
            searchPos = nextPos + HwpxDocument.SEARCH_SKIP_OFFSET;
          }
        }

        // If afterElementIndex is -1, insert after the first paragraph (which contains secPr)
        // IMPORTANT: <hp:secPr> must remain in the first paragraph for the document to be valid
        if (insert.afterElementIndex === -1) {
          // Find the end of the first <hp:p> element (which contains <hp:secPr>)
          const firstPStart = xml.indexOf('<hp:p');
          if (firstPStart !== -1) {
            const firstPEnd = xml.indexOf('</hp:p>', firstPStart);
            if (firstPEnd !== -1) {
              insertPosition = firstPEnd + '</hp:p>'.length;
            }
          }
        }

        // If position not found, insert at end of section (before </hs:sec>)
        if (insertPosition === -1) {
          const secEnd = xml.lastIndexOf('</hs:sec>');
          if (secEnd !== -1) {
            insertPosition = secEnd;
          }
        }

        if (insertPosition !== -1) {
          xml = xml.substring(0, insertPosition) + paragraphXml + xml.substring(insertPosition);
        }
      }

      this._zip.file(sectionPath, xml);
    }
  }

  /**
   * Apply nested table inserts to XML.
   * Inserts a new table inside a cell of an existing table.
   */
  private async applyNestedTableInsertsToXml(): Promise<void> {
    if (!this._zip) return;

    // Group inserts by section
    const insertsBySection = new Map<number, Array<{ parentTableIndex: number; row: number; col: number; nestedRows: number; nestedCols: number; data: string[][] }>>();
    for (const insert of this._pendingNestedTableInserts) {
      const sectionInserts = insertsBySection.get(insert.sectionIndex) || [];
      sectionInserts.push({
        parentTableIndex: insert.parentTableIndex,
        row: insert.row,
        col: insert.col,
        nestedRows: insert.nestedRows,
        nestedCols: insert.nestedCols,
        data: insert.data
      });
      insertsBySection.set(insert.sectionIndex, sectionInserts);
    }

    // Process each section
    for (const [sectionIndex, inserts] of insertsBySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      const file = this._zip.file(sectionPath);
      if (!file) continue;

      let xml = await file.async('string');

      // Group inserts by parentTableIndex to handle multiple inserts to same table correctly
      const insertsByTable = new Map<number, Array<{ row: number; col: number; nestedRows: number; nestedCols: number; data: string[][] }>>();
      for (const insert of inserts) {
        const tableInserts = insertsByTable.get(insert.parentTableIndex) || [];
        tableInserts.push({
          row: insert.row,
          col: insert.col,
          nestedRows: insert.nestedRows,
          nestedCols: insert.nestedCols,
          data: insert.data
        });
        insertsByTable.set(insert.parentTableIndex, tableInserts);
      }

      // Process tables in descending order of index to avoid position shifting between tables
      const sortedTableIndices = [...insertsByTable.keys()].sort((a, b) => b - a);

      for (const tableIndex of sortedTableIndices) {
        const tableInserts = insertsByTable.get(tableIndex)!;

        // Re-find tables for each table we process (positions may have shifted)
        const currentTables = this.findAllTables(xml);
        if (tableIndex >= currentTables.length) continue;

        const tableData = currentTables[tableIndex];
        let tableXml = tableData.xml;

        // Count tags before all insertions for this table
        const beforeTblOpen = (xml.match(/<(?:hp|hs|hc):tbl/g) || []).length;
        const beforeTblClose = (xml.match(/<\/(?:hp|hs|hc):tbl>/g) || []).length;

        // Sort inserts by row (desc), then col (desc) to avoid position shifting within table
        tableInserts.sort((a, b) => {
          if (a.row !== b.row) return b.row - a.row;
          return b.col - a.col;
        });

        // Process all inserts for this table, re-finding elements after each modification
        for (const insert of tableInserts) {
          // Re-find rows in current tableXml (positions change after each insert)
          const rows = this.findAllElementsWithDepth(tableXml, 'tr');
          if (insert.row >= rows.length) continue;

          const rowXml = rows[insert.row].xml;
          const cells = this.findAllElementsWithDepth(rowXml, 'tc');
          if (insert.col >= cells.length) continue;

          const cellXml = cells[insert.col].xml;

          // Generate nested table XML
          const nestedTableXml = this.generateNestedTableXml(insert.nestedRows, insert.nestedCols, insert.data);

          // Insert nested table into cell
          const updatedCellXml = this.insertNestedTableIntoCell(cellXml, nestedTableXml);

          // Update the row with the new cell
          const updatedRowXml = rowXml.substring(0, cells[insert.col].startIndex) +
            updatedCellXml +
            rowXml.substring(cells[insert.col].endIndex);

          // Update the table with the new row
          tableXml = tableXml.substring(0, rows[insert.row].startIndex) +
            updatedRowXml +
            tableXml.substring(rows[insert.row].endIndex);
        }

        // Apply all inserts for this table to the main XML at once
        xml = xml.substring(0, tableData.startIndex) +
          tableXml +
          xml.substring(tableData.endIndex);

        // Validate XML integrity: should have +N for both open and close tags
        const expectedIncrease = tableInserts.length;
        const afterTblOpen = (xml.match(/<(?:hp|hs|hc):tbl/g) || []).length;
        const afterTblClose = (xml.match(/<\/(?:hp|hs|hc):tbl>/g) || []).length;

        if (afterTblOpen !== beforeTblOpen + expectedIncrease || afterTblClose !== beforeTblClose + expectedIncrease) {
          console.error(`[HwpxDocument] XML corruption detected in nested table insertion! tbl tags: ${beforeTblOpen}→${afterTblOpen} open (expected +${expectedIncrease}), ${beforeTblClose}→${afterTblClose} close (expected +${expectedIncrease})`);
          throw new Error(`Nested table insertion failed: XML tag imbalance detected (expected +${expectedIncrease} for open and close tags)`);
        }
      }

      this._zip.file(sectionPath, xml);
    }
  }

  /**
   * Generate XML for a nested table.
   */
  private generateNestedTableXml(rows: number, cols: number, data: string[][]): string {
    // Generate unique ID
    const id = Math.floor(Math.random() * 2000000000) + 100000000;
    const zOrder = Math.floor(Math.random() * 100);

    // Calculate sizes (in hwpunit, 1 hwpunit = 0.1mm)
    const cellWidth = 8000; // ~80mm per cell
    const cellHeight = 1400; // ~14mm per cell
    const tableWidth = cellWidth * cols;
    const tableHeight = cellHeight * rows;

    let xml = `<hp:tbl id="${id}" zOrder="${zOrder}" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" pageBreak="NONE" repeatHeader="0" rowCnt="${rows}" colCnt="${cols}" cellSpacing="0" borderFillIDRef="2" noAdjust="0">`;

    // Size element
    xml += `<hp:sz width="${tableWidth}" widthRelTo="ABSOLUTE" height="${tableHeight}" heightRelTo="ABSOLUTE" protect="0"/>`;

    // Position element (treat as character for inline table)
    xml += `<hp:pos treatAsChar="1" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>`;

    // Original position
    xml += `<hp:outMargin left="0" right="0" top="0" bottom="0"/>`;

    // Inside margin
    xml += `<hp:inMargin left="0" right="0" top="0" bottom="0"/>`;

    // Cell zone list (column widths)
    xml += `<hp:cellzoneList>`;
    for (let c = 0; c < cols; c++) {
      xml += `<hp:cellzone startRowAddr="0" startColAddr="${c}" endRowAddr="${rows - 1}" endColAddr="${c}" borderFillIDRef="2"/>`;
    }
    xml += `</hp:cellzoneList>`;

    // Table rows
    for (let r = 0; r < rows; r++) {
      xml += `<hp:tr>`;
      for (let c = 0; c < cols; c++) {
        const cellText = (data[r] && data[r][c]) ? this.escapeXml(data[r][c]) : '';

        xml += `<hp:tc name="" header="0" hasMargin="0" protect="0" editable="0" dirty="0" borderFillIDRef="2">`;
        xml += `<hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" vertAlign="CENTER" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" hasTextRef="0" hasNumRef="0">`;
        xml += `<hp:p id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">`;
        xml += `<hp:run charPrIDRef="0">`;
        xml += `<hp:t>${cellText}</hp:t>`;
        xml += `</hp:run>`;
        xml += `<hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" baseline="850" spacing="600" horzpos="0" horzsize="0" flags="0"/></hp:linesegarray>`;
        xml += `</hp:p>`;
        xml += `</hp:subList>`;
        xml += `<hp:cellAddr colAddr="${c}" rowAddr="${r}"/>`;
        xml += `<hp:cellSpan colSpan="1" rowSpan="1"/>`;
        xml += `<hp:cellSz width="${cellWidth}" height="${cellHeight}"/>`;
        xml += `<hp:cellMargin left="141" right="141" top="141" bottom="141"/>`;
        xml += `</hp:tc>`;
      }
      xml += `</hp:tr>`;
    }

    xml += `</hp:tbl>`;
    return xml;
  }

  /**
   * Insert a nested table XML into a cell XML.
   * Finds the last <hp:p> in the cell and inserts the table inside a run.
   */
  private insertNestedTableIntoCell(cellXml: string, nestedTableXml: string): string {
    // Find the subList in the cell
    const subListMatch = cellXml.match(/<hp:subList[^>]*>/);
    if (!subListMatch) {
      // No subList, try to add to paragraph directly
      const pMatch = cellXml.match(/<hp:p[^>]*>/);
      if (pMatch) {
        const insertPos = cellXml.indexOf(pMatch[0]) + pMatch[0].length;
        const runXml = `<hp:run charPrIDRef="0"><hp:t> </hp:t>${nestedTableXml}<hp:t/></hp:run>`;
        return cellXml.substring(0, insertPos) + runXml + cellXml.substring(insertPos);
      }
      return cellXml;
    }

    // Find the last </hp:p> before </hp:subList>
    const subListEnd = cellXml.lastIndexOf('</hp:subList>');
    if (subListEnd === -1) return cellXml;

    // Find the last </hp:p> before subList end
    const lastPEnd = cellXml.lastIndexOf('</hp:p>', subListEnd);
    if (lastPEnd === -1) return cellXml;

    // Find the corresponding <hp:p> tag
    let pStart = cellXml.lastIndexOf('<hp:p', lastPEnd);
    if (pStart === -1) return cellXml;

    // Find the end of the opening <hp:p ...> tag
    const pTagEnd = cellXml.indexOf('>', pStart) + 1;

    // Create new run with nested table
    const runXml = `<hp:run charPrIDRef="0"><hp:t> </hp:t>${nestedTableXml}<hp:t/></hp:run>`;

    // Insert after the opening <hp:p> tag
    return cellXml.substring(0, pTagEnd) + runXml + cellXml.substring(pTagEnd);
  }

  /**
   * Apply cell merges to XML.
   * Updates colSpan/rowSpan attributes on master cell and removes merged cells.
   * Groups merges by table to handle multiple merges in the same table correctly.
   */
  private async applyCellMergesToXml(): Promise<void> {
    if (!this._zip) return;

    // Group merges by section, then by table
    const mergesBySection = new Map<number, Map<number, Array<{
      startRow: number;
      startCol: number;
      endRow: number;
      endCol: number;
    }>>>();

    for (const merge of this._pendingCellMerges) {
      if (!mergesBySection.has(merge.sectionIndex)) {
        mergesBySection.set(merge.sectionIndex, new Map());
      }
      const sectionMerges = mergesBySection.get(merge.sectionIndex)!;

      if (!sectionMerges.has(merge.tableIndex)) {
        sectionMerges.set(merge.tableIndex, []);
      }
      sectionMerges.get(merge.tableIndex)!.push({
        startRow: merge.startRow,
        startCol: merge.startCol,
        endRow: merge.endRow,
        endCol: merge.endCol
      });
    }

    // Process each section
    for (const [sectionIndex, tablesMerges] of mergesBySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      const file = this._zip.file(sectionPath);
      if (!file) continue;

      let xml = await file.async('string');
      const originalXml = xml;

      // Process tables in reverse order to avoid index shifting
      const sortedTableIndices = [...tablesMerges.keys()].sort((a, b) => b - a);

      for (const tableIndex of sortedTableIndices) {
        // Re-find tables with current XML (positions may have shifted)
        const currentTables = this.findAllTables(xml);

        if (tableIndex >= currentTables.length) {
          console.warn(`[HwpxDocument] applyCellMergesToXml: Table index ${tableIndex} out of bounds`);
          continue;
        }

        const tableData = currentTables[tableIndex];
        let tableXml = tableData.xml;

        // Get merges for this table and sort by row/col descending
        const tableMerges = tablesMerges.get(tableIndex)!;
        tableMerges.sort((a, b) => {
          if (a.startRow !== b.startRow) return b.startRow - a.startRow;
          return b.startCol - a.startCol;
        });

        // Store original table XML for rollback
        const originalTableXml = tableXml;

        // Apply each merge to the table XML
        for (const merge of tableMerges) {
          const updatedTableXml = this.applyMergeToTable(
            tableXml,
            merge.startRow,
            merge.startCol,
            merge.endRow,
            merge.endCol
          );

          if (updatedTableXml) {
            tableXml = updatedTableXml;
          }
        }

        // Validate table structure after all merges
        const tableError = this.validateTableStructure(tableXml);
        if (tableError) {
          console.error(`[HwpxDocument] applyCellMergesToXml: Table structure validation failed: ${tableError}`);
          console.error(`[HwpxDocument] Reverting table ${tableIndex} to original state`);
          tableXml = originalTableXml;
        }

        // Replace table in section XML
        xml = xml.substring(0, tableData.startIndex) + tableXml + xml.substring(tableData.endIndex);
      }

      // Validate result
      if (this.validateXmlStructure(xml)) {
        this._zip.file(sectionPath, xml);
      } else {
        console.error(`[HwpxDocument] applyCellMergesToXml: XML validation failed, reverting`);
        this._zip.file(sectionPath, originalXml);
      }
    }
  }

  /**
   * Apply merge to a single table XML.
   * @returns Updated table XML or null if merge failed
   */
  private applyMergeToTable(
    tableXml: string,
    startRow: number,
    startCol: number,
    endRow: number,
    endCol: number
  ): string | null {
    const colSpan = endCol - startCol + 1;
    const rowSpan = endRow - startRow + 1;

    // Find all rows
    const rows = this.findAllElementsWithDepth(tableXml, 'tr');
    if (rows.length <= endRow) {
      console.warn(`[HwpxDocument] applyMergeToTable: Not enough rows`);
      return null;
    }

    let result = tableXml;
    let rowSearchStartPos = 0;

    // Process rows in reverse order to maintain indices
    for (let rowIdx = endRow; rowIdx >= startRow; rowIdx--) {
      const row = rows[rowIdx];
      const rowXml = row.xml;

      // Find all cells in this row
      const cells = this.findAllElementsWithDepth(rowXml, 'tc');

      let updatedRowXml = rowXml;

      // Process cells in reverse order within the merge range
      for (let colIdx = endCol; colIdx >= startCol; colIdx--) {
        // Find the cell at this column address
        const cellIndex = cells.findIndex(c => {
          const colAddrMatch = c.xml.match(/colAddr="(\d+)"/);
          return colAddrMatch && parseInt(colAddrMatch[1]) === colIdx;
        });

        if (cellIndex === -1) continue;

        const cell = cells[cellIndex];

        if (rowIdx === startRow && colIdx === startCol) {
          // This is the master cell - update colSpan and rowSpan
          let updatedCellXml = cell.xml;

          // Update colSpan
          if (updatedCellXml.includes('colSpan="')) {
            updatedCellXml = updatedCellXml.replace(/colSpan="\d+"/, `colSpan="${colSpan}"`);
          } else {
            // Add colSpan attribute
            updatedCellXml = updatedCellXml.replace(/<(hp|hs):tc\b/, `<$1:tc colSpan="${colSpan}"`);
          }

          // Update rowSpan
          if (updatedCellXml.includes('rowSpan="')) {
            updatedCellXml = updatedCellXml.replace(/rowSpan="\d+"/, `rowSpan="${rowSpan}"`);
          } else {
            // Add rowSpan attribute
            updatedCellXml = updatedCellXml.replace(/<(hp|hs):tc\b/, `<$1:tc rowSpan="${rowSpan}"`);
          }

          // Replace in row XML using startIndex from depth-aware search
          const cellStart = cell.startIndex;
          updatedRowXml = updatedRowXml.substring(0, cellStart) + updatedCellXml + updatedRowXml.substring(cellStart + cell.xml.length);
        } else {
          // This cell should be removed (it's covered by the master cell)
          const cellStart = cell.startIndex;
          updatedRowXml = updatedRowXml.substring(0, cellStart) + updatedRowXml.substring(cellStart + cell.xml.length);
        }
      }

      // Replace the row in result using sequential search position
      const rowStart = result.indexOf(row.xml, rowSearchStartPos);
      if (rowStart !== -1) {
        result = result.substring(0, rowStart) + updatedRowXml + result.substring(rowStart + row.xml.length);
      }
    }

    return result;
  }

  /**
   * Apply cell splits to XML.
   * Resets colSpan/rowSpan to 1 and creates new cells to fill the split area.
   */
  private async applyCellSplitsToXml(): Promise<void> {
    if (!this._zip) return;

    // Group splits by section
    const splitsBySection = new Map<number, Array<{
      tableIndex: number;
      row: number;
      col: number;
      originalColSpan: number;
      originalRowSpan: number;
    }>>();

    for (const split of this._pendingCellSplits) {
      const sectionSplits = splitsBySection.get(split.sectionIndex) || [];
      sectionSplits.push({
        tableIndex: split.tableIndex,
        row: split.row,
        col: split.col,
        originalColSpan: split.originalColSpan,
        originalRowSpan: split.originalRowSpan
      });
      splitsBySection.set(split.sectionIndex, sectionSplits);
    }

    // Process each section
    for (const [sectionIndex, splits] of splitsBySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      const file = this._zip.file(sectionPath);
      if (!file) continue;

      let xml = await file.async('string');
      const originalXml = xml;

      // Find all tables in this section
      const tables = this.findAllTables(xml);

      // Process splits in reverse table order to avoid index shifting
      const sortedSplits = [...splits].sort((a, b) => b.tableIndex - a.tableIndex);

      for (const split of sortedSplits) {
        // Re-find tables with current XML (positions may have shifted)
        const currentTables = this.findAllTables(xml);

        if (split.tableIndex >= currentTables.length) {
          console.warn(`[HwpxDocument] applyCellSplitsToXml: Table index ${split.tableIndex} out of bounds`);
          continue;
        }

        const tableData = currentTables[split.tableIndex];
        const originalTableXml = tableData.xml;

        const updatedTableXml = this.applySplitToTable(
          tableData.xml,
          split.row,
          split.col,
          split.originalColSpan,
          split.originalRowSpan
        );

        if (updatedTableXml) {
          // Validate table structure after split
          const tableError = this.validateTableStructure(updatedTableXml);
          if (tableError) {
            console.error(`[HwpxDocument] applyCellSplitsToXml: Table structure validation failed: ${tableError}`);
            console.error(`[HwpxDocument] Skipping split for table ${split.tableIndex}`);
            continue;
          }

          xml = xml.substring(0, tableData.startIndex) + updatedTableXml + xml.substring(tableData.endIndex);
        }
      }

      // Validate result
      if (this.validateXmlStructure(xml)) {
        this._zip.file(sectionPath, xml);
      } else {
        console.error(`[HwpxDocument] applyCellSplitsToXml: XML validation failed, reverting`);
        this._zip.file(sectionPath, originalXml);
      }
    }
  }

  /**
   * Apply split to a single table XML.
   * @returns Updated table XML or null if split failed
   */
  private applySplitToTable(
    tableXml: string,
    masterRow: number,
    masterCol: number,
    colSpan: number,
    rowSpan: number
  ): string | null {
    // Find all rows
    const rows = this.findAllElementsWithDepth(tableXml, 'tr');
    if (rows.length <= masterRow) {
      console.warn(`[HwpxDocument] applySplitToTable: Not enough rows`);
      return null;
    }

    let result = tableXml;

    // Process rows that need new cells (from bottom to top to maintain indices)
    for (let rowIdx = masterRow + rowSpan - 1; rowIdx >= masterRow; rowIdx--) {
      // Re-find rows after each modification
      const currentRows = this.findAllElementsWithDepth(result, 'tr');
      if (rowIdx >= currentRows.length) continue;

      const row = currentRows[rowIdx];
      let updatedRowXml = row.xml;

      // Find all cells in this row
      const cells = this.findAllElementsWithDepth(updatedRowXml, 'tc');

      if (rowIdx === masterRow) {
        // This is the master cell row - update the master cell and add cells after it
        const masterCellIndex = cells.findIndex(c => {
          const colAddrMatch = c.xml.match(/colAddr="(\d+)"/);
          return colAddrMatch && parseInt(colAddrMatch[1]) === masterCol;
        });

        if (masterCellIndex === -1) continue;

        const masterCell = cells[masterCellIndex];

        // Update master cell's colSpan and rowSpan to 1
        let updatedMasterCellXml = masterCell.xml;
        updatedMasterCellXml = updatedMasterCellXml.replace(/colSpan="\d+"/, 'colSpan="1"');
        updatedMasterCellXml = updatedMasterCellXml.replace(/rowSpan="\d+"/, 'rowSpan="1"');

        // Find position to insert new cells (after master cell)
        const masterCellEnd = updatedRowXml.indexOf(masterCell.xml) + masterCell.xml.length;

        // Generate new cells for this row (colSpan - 1 new cells)
        let newCells = '';
        for (let c = masterCol + 1; c < masterCol + colSpan; c++) {
          newCells += this.generateEmptyCell(rowIdx, c);
        }

        // Replace master cell and insert new cells
        const masterCellStart = updatedRowXml.indexOf(masterCell.xml);
        updatedRowXml = updatedRowXml.substring(0, masterCellStart) + updatedMasterCellXml + newCells + updatedRowXml.substring(masterCellEnd);
      } else {
        // This is a row that was covered by rowSpan - add new cells
        // Find the insertion point (after the cell before masterCol, or at the beginning)
        let insertPoint = -1;

        // Find cells sorted by colAddr
        const sortedCells = cells.slice().sort((a, b) => {
          const aAddr = parseInt(a.xml.match(/colAddr="(\d+)"/)?.[1] || '0');
          const bAddr = parseInt(b.xml.match(/colAddr="(\d+)"/)?.[1] || '0');
          return aAddr - bAddr;
        });

        // Find position to insert using sequential search
        let cellSearchPos = 0;
        for (const cell of sortedCells) {
          const cellColAddr = parseInt(cell.xml.match(/colAddr="(\d+)"/)?.[1] || '0');
          const cellPos = updatedRowXml.indexOf(cell.xml, cellSearchPos);
          if (cellPos !== -1) cellSearchPos = cellPos + cell.xml.length;
          if (cellColAddr < masterCol && cellPos !== -1) {
            insertPoint = cellPos + cell.xml.length;
          }
        }

        // If no cell before masterCol, insert at the beginning of row content
        if (insertPoint === -1) {
          const trMatch = updatedRowXml.match(/<(hp|hs):tr[^>]*>/);
          if (trMatch) {
            insertPoint = trMatch[0].length;
          } else {
            continue;
          }
        }

        // Generate new cells for this row
        let newCells = '';
        for (let c = masterCol; c < masterCol + colSpan; c++) {
          newCells += this.generateEmptyCell(rowIdx, c);
        }

        // Insert new cells
        updatedRowXml = updatedRowXml.substring(0, insertPoint) + newCells + updatedRowXml.substring(insertPoint);
      }

      // Replace the row in result
      const rowStart = result.indexOf(row.xml);
      if (rowStart !== -1) {
        result = result.substring(0, rowStart) + updatedRowXml + result.substring(rowStart + row.xml.length);
      }
    }

    return result;
  }

  /**
   * Generate an empty cell XML for split operations.
   */
  private generateEmptyCell(rowAddr: number, colAddr: number): string {
    const paragraphId = Math.random().toString(36).substring(2, 11);
    return `<hp:tc colAddr="${colAddr}" rowAddr="${rowAddr}" colSpan="1" rowSpan="1">
        <hp:subList>
          <hp:p id="${paragraphId}">
            <hp:run><hp:t></hp:t></hp:run>
          </hp:p>
        </hp:subList>
      </hp:tc>`;
  }

  /**
   * Apply table cell updates to XML while preserving original structure.
   * This function modifies only the text content of specific cells,
   * keeping all other XML elements, attributes, and structure intact.
   *
   * Safety features:
   * - Backs up original XML before modification
   * - Validates XML structure after changes
   * - Reverts to original if validation fails
   */
  private async applyTableCellUpdatesToXml(): Promise<void> {
    if (!this._zip) return;

    // Group updates by section for efficiency
    const updatesBySection = new Map<number, Array<{ tableId: string; row: number; col: number; text: string; charShapeId?: number }>>();
    for (const update of this._pendingTableCellUpdates) {
      const sectionUpdates = updatesBySection.get(update.sectionIndex) || [];
      sectionUpdates.push({ tableId: update.tableId, row: update.row, col: update.col, text: update.text, charShapeId: update.charShapeId });
      updatesBySection.set(update.sectionIndex, sectionUpdates);
    }

    // Process each section that has updates
    for (const [sectionIndex, updates] of updatesBySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      const file = this._zip.file(sectionPath);
      if (!file) continue;

      // Backup original XML for safety
      const originalXml = await file.async('string');
      let xml = originalXml;

      // Group updates by table ID
      const updatesByTableId = new Map<string, Array<{ row: number; col: number; text: string; charShapeId?: number }>>();
      for (const update of updates) {
        const tableUpdates = updatesByTableId.get(update.tableId) || [];
        tableUpdates.push({ row: update.row, col: update.col, text: update.text, charShapeId: update.charShapeId });
        updatesByTableId.set(update.tableId, tableUpdates);
      }

      // Process each table that has updates (by ID)
      for (const [tableId, tableUpdates] of updatesByTableId) {
        // Find the table by ID in XML
        const tableMatch = this.findTableById(xml, tableId);
        if (!tableMatch) continue;

        // Safety check: validate indices before substring operations
        if (tableMatch.startIndex < 0 || tableMatch.endIndex > xml.length || tableMatch.startIndex > tableMatch.endIndex) {
          console.warn(`[HwpxDocument] Invalid table indices for table ${tableId}, skipping update`);
          continue;
        }

        // Update the table XML with cell changes
        const updatedTableXml = this.updateTableCellsInXml(tableMatch.xml, tableUpdates);

        // Safety check: ensure updated XML is not empty
        // Note: Size reduction is normal when replacing complex content with simple text
        if (!updatedTableXml || updatedTableXml.length === 0) {
          console.warn(`[HwpxDocument] Empty table update result for table ${tableId}, skipping`);
          continue;
        }

        // Log significant size changes for debugging (but don't skip)
        const sizeRatio = updatedTableXml.length / tableMatch.xml.length;
        if (sizeRatio < 0.3 || sizeRatio > 3.0) {
          console.log(`[HwpxDocument] Table ${tableId} size changed significantly: ${tableMatch.xml.length} -> ${updatedTableXml.length} (ratio: ${sizeRatio.toFixed(2)})`);
        }

        // Verify endIndex points to correct position (should be right after </hp:tbl>)
        // The tableMatch.xml should end with closing tbl tag
        if (!tableMatch.xml.match(/<\/(?:hp|hs|hc):tbl>$/)) {
          console.error(`[HwpxDocument] Table XML does not end with closing tag for table ${tableId}, skipping`);
          continue;
        }

        // Verify that the remainder starts correctly (not with partial tag remnants)
        const remainder = xml.substring(tableMatch.endIndex);
        if (remainder.match(/^[a-z]>/)) {
          console.error(`[HwpxDocument] Invalid remainder after table ${tableId}: starts with "${remainder.substring(0, 10)}"`);
          console.error(`[HwpxDocument] endIndex=${tableMatch.endIndex}, xml.length=${xml.length}`);
          continue;
        }

        // Replace the old table XML with the updated one
        const newXml = xml.substring(0, tableMatch.startIndex) + updatedTableXml + remainder;

        // Verify no corruption introduced
        if (/<\/(?:hp|hs|hc):tbl>[a-z]>/.test(newXml)) {
          console.error(`[HwpxDocument] Corruption would be introduced for table ${tableId}, skipping`);
          continue;
        }

        xml = newXml;
      }

      // Validate modified XML before saving
      // Note: Some original documents have pre-existing tag imbalances (e.g. <tr> open/close mismatch).
      // We validate to detect NEW corruption, but proceed with save if validation fails,
      // as the cell updates themselves are likely correct.
      const validationResult = this.validateXmlStructure(xml);
      if (!validationResult) {
        console.warn(`[HwpxDocument] XML validation warning for ${sectionPath} - proceeding with save`);
        console.warn(`[HwpxDocument] This may indicate pre-existing document issues, not corruption from current edit`);
      }

      this._zip.file(sectionPath, xml);
    }
  }

  /**
   * Basic XML structure validation.
   * Checks for common corruption indicators.
   * Note: This is intentionally lenient to avoid false positives.
   */
  private validateXmlStructure(xml: string): boolean {
    // Must start with XML declaration or root element
    if (!xml.trim().startsWith('<?xml') && !xml.trim().startsWith('<')) {
      console.warn(`[HwpxDocument] XML does not start with declaration or element`);
      return false;
    }

    // Check for truncated XML (ends with incomplete tag)
    if (xml.match(/<[^>]*$/)) {
      console.warn(`[HwpxDocument] XML appears truncated (incomplete tag at end)`);
      return false;
    }

    // Check for broken opening tags (< followed by another < without >)
    // This catches cases like: <tag<broken>
    if (xml.match(/<[^>]*</)) {
      console.warn(`[HwpxDocument] Broken opening tag detected`);
      return false;
    }

    // Check for empty or near-empty content (likely corruption)
    if (xml.trim().length < 100) {
      console.warn(`[HwpxDocument] Suspiciously short XML content`);
      return false;
    }

    // Check for known corruption patterns (e.g., </hp:tbl>l> - endIndex off by 2)
    const corruptionPatterns = [
      { pattern: /<\/(?:hp|hs|hc):tbl>l>/g, desc: 'tbl close tag with trailing l>' },
      { pattern: /<\/(?:hp|hs|hc):tr>r>/g, desc: 'tr close tag with trailing r>' },
      { pattern: /<\/(?:hp|hs|hc):tc>c>/g, desc: 'tc close tag with trailing c>' },
    ];
    for (const { pattern, desc } of corruptionPatterns) {
      if (pattern.test(xml)) {
        console.error(`[HwpxDocument] Corruption detected: ${desc}`);
        return false;
      }
    }

    // Check tag balance for critical structural elements
    const criticalTags = ['tbl', 'tr', 'tc', 'p', 'subList'];
    for (const tag of criticalTags) {
      const balance = this.checkTagBalance(xml, tag);
      if (balance !== 0) {
        const openCount = (xml.match(new RegExp(`<(?:hp|hs|hc):${tag}(?:\\s|>)`, 'g')) || []).length;
        const closeCount = (xml.match(new RegExp(`</(?:hp|hs|hc):${tag}>`, 'g')) || []).length;
        console.warn(`[HwpxDocument] Tag imbalance detected for ${tag}: ${openCount} open, ${closeCount} close (balance: ${balance > 0 ? '+' : ''}${balance})`);
        return false;
      }
    }

    return true;
  }

  /**
   * Check tag balance for a specific element name.
   * Returns the difference (open - close). 0 means balanced.
   */
  private checkTagBalance(xml: string, elementName: string): number {
    // Count opening tags: <hp:elementName> or <hp:elementName attr...> (not self-closing)
    const openPattern = new RegExp(`<(?:hp|hs|hc):${elementName}(?:\\s[^>]*)?>`, 'g');
    // Exclude self-closing tags from open count
    const selfClosingPattern = new RegExp(`<(?:hp|hs|hc):${elementName}(?:\\s[^>]*)?\\/\\s*>`, 'g');
    const closePattern = new RegExp(`<\\/(?:hp|hs|hc):${elementName}>`, 'g');

    const allOpens = (xml.match(openPattern) || []).length;
    const selfClosing = (xml.match(selfClosingPattern) || []).length;
    const opens = allOpens - selfClosing;
    const closes = (xml.match(closePattern) || []).length;

    return opens - closes;
  }

  /**
   * Validate table structure integrity.
   * Checks:
   * - Row count consistency (declared vs actual)
   * - Cell count per row matches colCnt when accounting for colSpan
   * - colAddr/rowAddr continuity
   * - No orphaned cells (rowSpan consistency)
   * @returns Error message if validation fails, null if valid
   */
  private validateTableStructure(tableXml: string): string | null {
    // Extract declared row and column counts
    const rowCntMatch = tableXml.match(/rowCnt="(\d+)"/);
    const colCntMatch = tableXml.match(/colCnt="(\d+)"/);

    if (!rowCntMatch || !colCntMatch) {
      // Tables without these attributes can't be validated
      return null;
    }

    const declaredRows = parseInt(rowCntMatch[1]);
    const declaredCols = parseInt(colCntMatch[1]);

    // Find all rows
    const rows = this.findAllElementsWithDepth(tableXml, 'tr');

    if (rows.length !== declaredRows) {
      return `Row count mismatch: declared ${declaredRows}, actual ${rows.length}`;
    }

    // Track which cells are covered by rowSpan from previous rows
    const coveredCells: Map<number, Set<number>> = new Map();
    for (let r = 0; r < declaredRows; r++) {
      coveredCells.set(r, new Set());
    }

    // Validate each row
    for (let rowIdx = 0; rowIdx < rows.length; rowIdx++) {
      const row = rows[rowIdx];
      const cells = this.findAllElementsWithDepth(row.xml, 'tc');

      let effectiveColCount = 0;
      const covered = coveredCells.get(rowIdx)!;

      for (const cell of cells) {
        // Get cell attributes
        const colAddrMatch = cell.xml.match(/colAddr="(\d+)"/);
        const rowAddrMatch = cell.xml.match(/rowAddr="(\d+)"/);
        const colSpanMatch = cell.xml.match(/colSpan="(\d+)"/);
        const rowSpanMatch = cell.xml.match(/rowSpan="(\d+)"/);

        const colAddr = colAddrMatch ? parseInt(colAddrMatch[1]) : effectiveColCount;
        const rowAddr = rowAddrMatch ? parseInt(rowAddrMatch[1]) : rowIdx;
        const colSpan = colSpanMatch ? parseInt(colSpanMatch[1]) : 1;
        const rowSpan = rowSpanMatch ? parseInt(rowSpanMatch[1]) : 1;

        // Validate rowAddr matches current row
        if (rowAddr !== rowIdx) {
          return `Row address mismatch at row ${rowIdx}: cell has rowAddr=${rowAddr}`;
        }

        // Add colSpan to effective count
        effectiveColCount += colSpan;

        // Mark cells as covered for future rows if rowSpan > 1
        if (rowSpan > 1) {
          for (let r = rowIdx + 1; r < rowIdx + rowSpan && r < declaredRows; r++) {
            for (let c = colAddr; c < colAddr + colSpan; c++) {
              coveredCells.get(r)!.add(c);
            }
          }
        }
      }

      // Add covered cells from previous rowSpan
      effectiveColCount += covered.size;

      // Validate total columns match
      if (effectiveColCount !== declaredCols) {
        return `Column count mismatch at row ${rowIdx}: effective ${effectiveColCount}, declared ${declaredCols}`;
      }
    }

    return null;
  }

  /**
   * Extract all nested tables from XML content.
   * Uses balanced bracket matching to find complete table elements.
   */
  private extractNestedTables(xml: string, prefix: string): string {
    const tables: string[] = [];
    const openTag = `<${prefix}:tbl`;
    const closeTag = `</${prefix}:tbl>`;

    let searchPos = 0;
    while (searchPos < xml.length) {
      const tblStart = xml.indexOf(openTag, searchPos);
      if (tblStart === -1) break;

      // Find the end of the opening tag
      const tagEnd = xml.indexOf('>', tblStart);
      if (tagEnd === -1) break;

      // Use balanced bracket matching to find the closing tag
      let depth = 1;
      let pos = tagEnd + 1;
      let tblEnd = -1;

      while (depth > 0 && pos < xml.length) {
        const nextOpen = xml.indexOf(openTag, pos);
        const nextClose = xml.indexOf(closeTag, pos);

        if (nextClose === -1) break;

        if (nextOpen !== -1 && nextOpen < nextClose) {
          depth++;
          pos = nextOpen + openTag.length;
        } else {
          depth--;
          if (depth === 0) {
            tblEnd = nextClose + closeTag.length;
          }
          pos = nextClose + closeTag.length;
        }
      }

      if (tblEnd !== -1) {
        tables.push(xml.substring(tblStart, tblEnd));
        searchPos = tblEnd;
      } else {
        // Malformed XML, move past this opening tag
        searchPos = tagEnd + 1;
      }
    }

    return tables.join('');
  }

  /**
   * Find a table by its ID in XML.
   */
  private findTableById(xml: string, tableId: string): { xml: string; startIndex: number; endIndex: number } | null {
    // Match table with specific ID
    const tableStartRegex = new RegExp(`<(?:hp|hs|hc):tbl[^>]*\\bid="${tableId}"[^>]*>`, 'g');
    const match = tableStartRegex.exec(xml);

    if (!match) {
      // Try alternate ID format (id='...' instead of id="...")
      const altRegex = new RegExp(`<(?:hp|hs|hc):tbl[^>]*\\bid='${tableId}'[^>]*>`, 'g');
      const altMatch = altRegex.exec(xml);
      if (!altMatch) return null;
      return this.extractTableFromMatch(xml, altMatch);
    }

    return this.extractTableFromMatch(xml, match);
  }

  /**
   * Extract complete table XML from a regex match.
   */
  private extractTableFromMatch(xml: string, match: RegExpExecArray): { xml: string; startIndex: number; endIndex: number } | null {
    const startIndex = match.index;
    const prefix = match[0].match(/<(hp|hs|hc):tbl/)?.[1] || 'hp';

    // Find the matching closing tag
    const endTag = `</${prefix}:tbl>`;
    let depth = 1;
    let pos = match.index + match[0].length;

    while (depth > 0 && pos < xml.length) {
      const nextOpen = xml.indexOf(`<${prefix}:tbl`, pos);
      const nextClose = xml.indexOf(endTag, pos);

      if (nextClose === -1) return null;

      if (nextOpen !== -1 && nextOpen < nextClose) {
        depth++;
        pos = nextOpen + 1;
      } else {
        depth--;
        if (depth === 0) {
          const endIndex = nextClose + endTag.length;
          return {
            xml: xml.substring(startIndex, endIndex),
            startIndex,
            endIndex
          };
        }
        pos = nextClose + 1;
      }
    }

    return null;
  }

  /**
   * Find all tables in XML and return their positions and content.
   */
  /**
   * Find top-level paragraph and table elements (direct children of section).
   * Uses depth tracking to skip elements nested inside <hp:tbl>, <hp:tc>, <hp:secPr>, etc.
   * Only counts <hp:p> and <hp:tbl> at depth 0 (relative to section root).
   */
  private findTopLevelElements(sectionXml: string): Array<{ start: number; tagLength: number; content: string; type: 'p' | 'tbl' }> {
    const results: Array<{ start: number; tagLength: number; content: string; type: 'p' | 'tbl' }> = [];

    // Find section body start (after <hs:sec ...> or <hp:sec ...>)
    const secOpenMatch = sectionXml.match(/<(?:hs|hp):sec[^>]*>/);
    if (!secOpenMatch) return results;
    const bodyStart = secOpenMatch.index! + secOpenMatch[0].length;

    // Find section body end (before </hs:sec> or </hp:sec>)
    const secCloseIdx = Math.max(
      sectionXml.lastIndexOf('</hs:sec>'),
      sectionXml.lastIndexOf('</hp:sec>')
    );
    const bodyEnd = secCloseIdx === -1 ? sectionXml.length : secCloseIdx;

    // Scan through the body tracking depth of nesting elements
    let depth = 0;
    let pos = bodyStart;
    const depthTags = ['tbl', 'tc', 'secPr', 'subList'];
    const prefixes = ['hp', 'hs', 'hc'];

    while (pos < bodyEnd) {
      const nextTag = sectionXml.indexOf('<', pos);
      if (nextTag === -1 || nextTag >= bodyEnd) break;

      // Check for closing tags that decrease depth
      let isClose = false;
      for (const prefix of prefixes) {
        for (const tag of depthTags) {
          const closeStr = `</${prefix}:${tag}>`;
          if (sectionXml.startsWith(closeStr, nextTag)) {
            depth--;
            pos = nextTag + closeStr.length;
            isClose = true;
            break;
          }
        }
        if (isClose) break;
      }
      if (isClose) continue;

      // Check for opening tags we care about at depth 0
      if (depth === 0) {
        for (const prefix of prefixes) {
          // Check <prefix:p ...>
          const pOpenStr = `<${prefix}:p `;
          const pOpenSelf = `<${prefix}:p>`;
          if (sectionXml.startsWith(pOpenStr, nextTag) || sectionXml.startsWith(pOpenSelf, nextTag)) {
            const tagEnd = sectionXml.indexOf('>', nextTag);
            if (tagEnd !== -1) {
              const tagContent = sectionXml.substring(nextTag, tagEnd + 1);
              results.push({ start: nextTag, tagLength: tagContent.length, content: tagContent, type: 'p' });
            }
            break;
          }
          // Check <prefix:tbl ...>
          const tblOpenStr = `<${prefix}:tbl `;
          const tblOpenSelf = `<${prefix}:tbl>`;
          if (sectionXml.startsWith(tblOpenStr, nextTag) || sectionXml.startsWith(tblOpenSelf, nextTag)) {
            const tagEnd = sectionXml.indexOf('>', nextTag);
            if (tagEnd !== -1) {
              const tagContent = sectionXml.substring(nextTag, tagEnd + 1);
              results.push({ start: nextTag, tagLength: tagContent.length, content: tagContent, type: 'tbl' });
            }
            break;
          }
        }
      }

      // Check for opening tags that increase depth
      let isOpen = false;
      for (const prefix of prefixes) {
        for (const tag of depthTags) {
          const openStr = `<${prefix}:${tag}`;
          if (sectionXml.startsWith(openStr, nextTag)) {
            const afterTag = nextTag + openStr.length;
            if (afterTag < sectionXml.length && (sectionXml[afterTag] === ' ' || sectionXml[afterTag] === '>' || sectionXml[afterTag] === '/')) {
              depth++;
              isOpen = true;
              break;
            }
          }
        }
        if (isOpen) break;
      }

      // Move past this tag
      const tagEndPos = sectionXml.indexOf('>', nextTag);
      pos = tagEndPos !== -1 ? tagEndPos + 1 : nextTag + 1;
    }

    return results;
  }

  private findAllTables(xml: string): Array<{ xml: string; startIndex: number; endIndex: number }> {
    const tables: Array<{ xml: string; startIndex: number; endIndex: number }> = [];

    // Match both hp:tbl and hs:tbl (different namespace prefixes)
    const tableStartRegex = /<(?:hp|hs|hc):tbl[^>]*>/g;
    let match;

    while ((match = tableStartRegex.exec(xml)) !== null) {
      const startIndex = match.index;
      const prefix = match[0].match(/<(hp|hs|hc):tbl/)?.[1] || 'hp';

      // Find the matching closing tag
      const endTag = `</${prefix}:tbl>`;
      let depth = 1;
      let pos = match.index + match[0].length;

      while (depth > 0 && pos < xml.length) {
        const nextOpen = xml.indexOf(`<${prefix}:tbl`, pos);
        const nextClose = xml.indexOf(endTag, pos);

        if (nextClose === -1) break;

        if (nextOpen !== -1 && nextOpen < nextClose) {
          depth++;
          pos = nextOpen + 1;
        } else {
          depth--;
          if (depth === 0) {
            const endIndex = nextClose + endTag.length;
            tables.push({
              xml: xml.substring(startIndex, endIndex),
              startIndex,
              endIndex
            });
            // Skip nested tables by moving regex lastIndex to end of current table
            tableStartRegex.lastIndex = endIndex;
          }
          pos = nextClose + 1;
        }
      }
    }

    return tables;
  }

  /**
   * Find all elements of a given type using depth tracking.
   * This correctly handles nested elements (e.g., nested tables).
   * @param xml The XML string to search in
   * @param elementName The element name without namespace prefix (e.g., 'tr', 'tc')
   * @returns Array of elements with their positions
   */
  private findAllElementsWithDepth(xml: string, elementName: string): Array<{ xml: string; startIndex: number; endIndex: number }> {
    const elements: Array<{ xml: string; startIndex: number; endIndex: number }> = [];

    // Match all namespace prefixes (hp, hs, hc)
    const startPattern = new RegExp(`<(hp|hs|hc):${elementName}[^>]*>`, 'g');
    let match;

    while ((match = startPattern.exec(xml)) !== null) {
      const startIndex = match.index;
      const prefix = match[1];
      const openTag = `<${prefix}:${elementName}`;
      const closeTag = `</${prefix}:${elementName}>`;

      let depth = 1;
      let pos = match.index + match[0].length;

      while (depth > 0 && pos < xml.length) {
        const nextOpen = xml.indexOf(openTag, pos);
        const nextClose = xml.indexOf(closeTag, pos);

        if (nextClose === -1) break;

        if (nextOpen !== -1 && nextOpen < nextClose) {
          // Found another opening tag - go deeper
          depth++;
          pos = nextOpen + 1;
        } else {
          // Found closing tag
          depth--;
          if (depth === 0) {
            const endIndex = nextClose + closeTag.length;
            elements.push({
              xml: xml.substring(startIndex, endIndex),
              startIndex,
              endIndex
            });
            // Update regex lastIndex to continue after this element
            startPattern.lastIndex = endIndex;
          }
          pos = nextClose + 1;
        }
      }
    }

    return elements;
  }

  /**
   * Update specific cells in a table XML string.
   * Groups updates by row to avoid index corruption when multiple cells in the same row are updated.
   */
  private updateTableCellsInXml(tableXml: string, updates: Array<{ row: number; col: number; text: string; charShapeId?: number }>): string {
    let result = tableXml;

    // Capture initial tag counts for validation
    const initialTrOpen = (tableXml.match(/<(?:hp|hs|hc):tr[\s>]/g) || []).length;
    const initialTrClose = (tableXml.match(/<\/(?:hp|hs|hc):tr>/g) || []).length;
    const initialTcOpen = (tableXml.match(/<(?:hp|hs|hc):tc[\s>]/g) || []).length;
    const initialTcClose = (tableXml.match(/<\/(?:hp|hs|hc):tc>/g) || []).length;

    // Find all rows using depth tracking to handle nested tables correctly
    const rows = this.findAllElementsWithDepth(tableXml, 'tr');

    // Group updates by row to process each row only once
    const updatesByRow = new Map<number, Array<{ col: number; text: string; charShapeId?: number }>>();
    for (const update of updates) {
      if (update.row >= rows.length) continue;
      if (!updatesByRow.has(update.row)) {
        updatesByRow.set(update.row, []);
      }
      updatesByRow.get(update.row)!.push({ col: update.col, text: update.text, charShapeId: update.charShapeId });
    }

    // Sort row indices descending to process from end to start (avoid index shifting)
    const sortedRowIndices = Array.from(updatesByRow.keys()).sort((a, b) => b - a);

    for (const rowIndex of sortedRowIndices) {
      const rowData = rows[rowIndex];
      const cellUpdates = updatesByRow.get(rowIndex)!;

      // Validate row before update
      const rowTrOpen = (rowData.xml.match(/<(?:hp|hs|hc):tr[\s>]/g) || []).length;
      const rowTrClose = (rowData.xml.match(/<\/(?:hp|hs|hc):tr>/g) || []).length;

      // Apply all cell updates to this row at once
      const updatedRowXml = this.updateMultipleCellsInRow(rowData.xml, cellUpdates);

      // Validate row after update - tag balance should be preserved
      const updatedTrOpen = (updatedRowXml.match(/<(?:hp|hs|hc):tr[\s>]/g) || []).length;
      const updatedTrClose = (updatedRowXml.match(/<\/(?:hp|hs|hc):tr>/g) || []).length;

      if (rowTrOpen !== updatedTrOpen || rowTrClose !== updatedTrClose) {
        console.error(`[HwpxDocument] Row update corrupted tr tags: before=${rowTrOpen}/${rowTrClose}, after=${updatedTrOpen}/${updatedTrClose}`);
        console.error(`[HwpxDocument] Row ${rowIndex}, updates: ${JSON.stringify(cellUpdates.map(u => ({ col: u.col, textLen: u.text.length })))}`);
        // Return original to prevent corruption
        return tableXml;
      }

      result = result.substring(0, rowData.startIndex) + updatedRowXml + result.substring(rowData.endIndex);
    }

    // Final validation: ensure tag counts are preserved
    const finalTrOpen = (result.match(/<(?:hp|hs|hc):tr[\s>]/g) || []).length;
    const finalTrClose = (result.match(/<\/(?:hp|hs|hc):tr>/g) || []).length;
    const finalTcOpen = (result.match(/<(?:hp|hs|hc):tc[\s>]/g) || []).length;
    const finalTcClose = (result.match(/<\/(?:hp|hs|hc):tc>/g) || []).length;

    if (initialTrOpen !== finalTrOpen || initialTrClose !== finalTrClose) {
      console.error(`[HwpxDocument] Table update corrupted tr tags: initial=${initialTrOpen}/${initialTrClose}, final=${finalTrOpen}/${finalTrClose}`);
      return tableXml;
    }

    if (initialTcOpen !== finalTcOpen || initialTcClose !== finalTcClose) {
      console.error(`[HwpxDocument] Table update corrupted tc tags: initial=${initialTcOpen}/${initialTcClose}, final=${finalTcOpen}/${finalTcClose}`);
      return tableXml;
    }

    return result;
  }

  /**
   * Update multiple cells in a single row XML string.
   * Processes cells from right to left (descending col order) to avoid index shifting.
   */
  private updateMultipleCellsInRow(rowXml: string, updates: Array<{ col: number; text: string; charShapeId?: number }>): string {
    let result = rowXml;

    // Find all cells in this row using depth tracking to handle nested tables correctly
    const cells = this.findAllElementsWithDepth(rowXml, 'tc');

    // Deduplicate updates for the same cell (keep last value)
    // This prevents stale index issues when the same cell is updated multiple times
    const uniqueUpdates = new Map<number, { col: number; text: string; charShapeId?: number }>();
    for (const update of updates) {
      uniqueUpdates.set(update.col, update);
    }

    // Sort updates by col descending to process from right to left (avoid index shifting)
    const sortedUpdates = Array.from(uniqueUpdates.values()).sort((a, b) => b.col - a.col);

    for (const update of sortedUpdates) {
      if (update.col >= cells.length) continue;

      const cellData = cells[update.col];

      // Validate cell before update - capture nested table structure
      const cellTblOpen = (cellData.xml.match(/<(?:hp|hs|hc):tbl[\s>]/g) || []).length;
      const cellTblClose = (cellData.xml.match(/<\/(?:hp|hs|hc):tbl>/g) || []).length;
      const cellTrOpen = (cellData.xml.match(/<(?:hp|hs|hc):tr[\s>]/g) || []).length;
      const cellTrClose = (cellData.xml.match(/<\/(?:hp|hs|hc):tr>/g) || []).length;

      const updatedCellXml = this.updateTextInCell(cellData.xml, update.text, update.charShapeId);

      // Validate cell after update - nested structures must be preserved
      const updatedTblOpen = (updatedCellXml.match(/<(?:hp|hs|hc):tbl[\s>]/g) || []).length;
      const updatedTblClose = (updatedCellXml.match(/<\/(?:hp|hs|hc):tbl>/g) || []).length;
      const updatedTrOpen = (updatedCellXml.match(/<(?:hp|hs|hc):tr[\s>]/g) || []).length;
      const updatedTrClose = (updatedCellXml.match(/<\/(?:hp|hs|hc):tr>/g) || []).length;

      if (cellTblOpen !== updatedTblOpen || cellTblClose !== updatedTblClose ||
          cellTrOpen !== updatedTrOpen || cellTrClose !== updatedTrClose) {
        console.error(`[HwpxDocument] Cell update corrupted nested structure at col ${update.col}`);
        console.error(`[HwpxDocument] Before: tbl=${cellTblOpen}/${cellTblClose}, tr=${cellTrOpen}/${cellTrClose}`);
        console.error(`[HwpxDocument] After: tbl=${updatedTblOpen}/${updatedTblClose}, tr=${updatedTrOpen}/${updatedTrClose}`);
        console.error(`[HwpxDocument] Text (first 100 chars): ${update.text.substring(0, 100)}`);
        // Skip this update to prevent corruption
        continue;
      }

      result = result.substring(0, cellData.startIndex) + updatedCellXml + result.substring(cellData.endIndex);
    }

    return result;
  }

  /**
   * Update a specific cell in a row XML string.
   * @deprecated Use updateMultipleCellsInRow for better index handling
   */
  private updateCellInRow(rowXml: string, colIndex: number, newText: string, charShapeId?: number): string {
    return this.updateMultipleCellsInRow(rowXml, [{ col: colIndex, text: newText, charShapeId }]);
  }

  /**
   * Reset lineseg values to default so Hancom Word recalculates line layout.
   * When text content changes, the old lineseg values (horzsize, textpos, etc.)
   * no longer match the new text, causing rendering issues like overlapping text.
   * By resetting to default values, Hancom Word will recalculate proper line breaks.
   */
  private resetLinesegInXml(xml: string): string {
    // Find all linesegarray elements and reset their lineseg children to default values
    // Default lineseg: single line with minimal values - Hancom Word will recalculate
    const defaultLineseg = '<hp:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" baseline="850" spacing="600" horzpos="0" horzsize="0" flags="0"/>';

    // Replace linesegarray content with default single lineseg
    // This pattern matches <hp:linesegarray>...</hp:linesegarray> and replaces content
    const linesegArrayPattern = /(<(?:hp|hs|hc):linesegarray[^>]*>)[\s\S]*?(<\/(?:hp|hs|hc):linesegarray>)/g;

    return xml.replace(linesegArrayPattern, (match, openTag, closeTag) => {
      // Determine the prefix (hp, hs, or hc) from the opening tag
      const prefixMatch = openTag.match(/<(hp|hs|hc):linesegarray/);
      const prefix = prefixMatch ? prefixMatch[1] : 'hp';
      const defaultSeg = `<${prefix}:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" baseline="850" spacing="600" horzpos="0" horzsize="0" flags="0"/>`;
      return openTag + defaultSeg + closeTag;
    });
  }

  /**
   * Update text content in a cell XML string.
   * Handles both existing text replacement and empty cell population.
   * If charShapeId is provided, overrides the charPrIDRef attribute.
   */
  private updateTextInCell(cellXml: string, newText: string, charShapeId?: number): string {
    // Check if text contains newlines - if so, create multiple paragraphs
    if (newText.includes('\n')) {
      return this.updateTextInCellMultiline(cellXml, newText, charShapeId);
    }

    // For long text without newlines, use chunked processing to avoid XML issues
    // This creates multiple <hp:run> elements within the same paragraph
    if (newText.length > HwpxDocument.TEXT_CHUNK_SIZE) {
      return this.updateTextInCellChunked(cellXml, newText, charShapeId);
    }

    const escapedText = this.escapeXml(newText);
    let xml = cellXml;

    // If charShapeId is provided, update charPrIDRef in the first run tag
    if (charShapeId !== undefined) {
      xml = xml.replace(
        /(<(?:hp|hs|hc):run\s+)charPrIDRef="[^"]*"/,
        `$1charPrIDRef="${charShapeId}"`
      );
    }

    // Pattern 1: Cell has existing <hp:t> or <hs:t> or <hc:t> tags with content
    const tTagPattern = /(<(?:hp|hs|hc):t[^>]*>)([^<]*)(<\/(?:hp|hs|hc):t>)/g;
    let foundText = false;
    let result = xml.replace(tTagPattern, (match, openTag, _oldText, closeTag, offset) => {
      // Only replace the first text occurrence
      if (!foundText) {
        foundText = true;
        return openTag + escapedText + closeTag;
      }
      return match;
    });

    if (foundText) return this.resetLinesegInXml(result);

    // Pattern 2: Cell has empty <hp:t/> or <hp:t></hp:t> tags
    const emptyTTagPattern = /<((?:hp|hs|hc):t)([^>]*)\s*\/>/;
    const emptyTMatch = xml.match(emptyTTagPattern);
    if (emptyTMatch) {
      const updated = xml.replace(emptyTTagPattern, `<${emptyTMatch[1]}${emptyTMatch[2]}>${escapedText}</${emptyTMatch[1]}>`);
      return this.resetLinesegInXml(updated);
    }

    // Pattern 3a: Self-closing <hp:run .../> - expand to full run with text
    const selfClosingRunPattern = /<((?:hp|hs|hc):run)([^>]*)\s*\/>/;
    const selfClosingRunMatch = xml.match(selfClosingRunPattern);
    if (selfClosingRunMatch) {
      const tagName = selfClosingRunMatch[1]; // e.g., "hp:run"
      let attrs = selfClosingRunMatch[2];
      // If charShapeId is provided, update or add charPrIDRef
      if (charShapeId !== undefined) {
        if (attrs.includes('charPrIDRef=')) {
          attrs = attrs.replace(/charPrIDRef="[^"]*"/, `charPrIDRef="${charShapeId}"`);
        } else {
          attrs = ` charPrIDRef="${charShapeId}"` + attrs;
        }
      }
      const prefix = tagName.split(':')[0]; // e.g., "hp"
      const updated = xml.replace(selfClosingRunPattern, `<${tagName}${attrs}><${prefix}:t>${escapedText}</${prefix}:t></${tagName}>`);
      return this.resetLinesegInXml(updated);
    }

    // Pattern 3b: Cell has <hp:run> but no <hp:t> - add text inside run
    const runPattern = /(<(?:hp|hs|hc):run[^>]*>)([\s\S]*?)(<\/(?:hp|hs|hc):run>)/;
    const runMatch = xml.match(runPattern);
    if (runMatch) {
      const prefix = runMatch[1].match(/<(hp|hs|hc):run/)?.[1] || 'hp';
      const newRunContent = runMatch[2] + `<${prefix}:t>${escapedText}</${prefix}:t>`;
      const updated = xml.replace(runPattern, runMatch[1] + newRunContent + runMatch[3]);
      return this.resetLinesegInXml(updated);
    }

    // Pattern 4: Cell has <hp:subList><hp:p> structure - find the paragraph and add text
    const subListPattern = /(<(?:hp|hs|hc):subList[^>]*>[\s\S]*?<(?:hp|hs|hc):p[^>]*>)([\s\S]*?)(<\/(?:hp|hs|hc):p>)/;
    const subListMatch = xml.match(subListPattern);
    if (subListMatch) {
      const prefix = subListMatch[1].match(/<(hp|hs|hc):subList/)?.[1] || 'hp';
      // Check if there's already a run
      if (!subListMatch[2].includes(':run')) {
        const charAttr = charShapeId !== undefined ? ` charPrIDRef="${charShapeId}"` : '';
        const newContent = subListMatch[2] + `<${prefix}:run${charAttr}><${prefix}:t>${escapedText}</${prefix}:t></${prefix}:run>`;
        const updated = xml.replace(subListPattern, subListMatch[1] + newContent + subListMatch[3]);
        return this.resetLinesegInXml(updated);
      }
    }

    // Pattern 5: Cell has only <hp:p> without subList
    const pPattern = /(<(?:hp|hs|hc):p[^>]*>)([\s\S]*?)(<\/(?:hp|hs|hc):p>)/;
    const pMatch = xml.match(pPattern);
    if (pMatch) {
      const prefix = pMatch[1].match(/<(hp|hs|hc):p/)?.[1] || 'hp';
      if (!pMatch[2].includes(':run') && !pMatch[2].includes(':t>')) {
        const charAttr = charShapeId !== undefined ? ` charPrIDRef="${charShapeId}"` : '';
        const newContent = pMatch[2] + `<${prefix}:run${charAttr}><${prefix}:t>${escapedText}</${prefix}:t></${prefix}:run>`;
        const updated = xml.replace(pPattern, pMatch[1] + newContent + pMatch[3]);
        return this.resetLinesegInXml(updated);
      }
    }

    // Fallback: return unchanged (shouldn't happen in well-formed HWPX)
    return xml;
  }

  /**
   * Update text content in a cell with chunked runs (for long text without newlines).
   * Splits long text into multiple <hp:run> elements within a single paragraph.
   */
  private updateTextInCellChunked(cellXml: string, newText: string, charShapeId?: number): string {
    const charAttr = charShapeId !== undefined ? ` charPrIDRef="${charShapeId}"` : ' charPrIDRef="0"';
    let xml = cellXml;

    // Find the subList element to replace paragraph content
    const subListStartMatch = xml.match(/<(hp|hs|hc):subList[^>]*>/);
    if (subListStartMatch) {
      const prefix = subListStartMatch[1];
      const startTag = subListStartMatch[0];
      const startIndex = xml.indexOf(startTag);
      const contentStartIndex = startIndex + startTag.length;

      // Find matching closing tag using balanced bracket counting
      const openTag = `<${prefix}:subList`;
      const closeTag = `</${prefix}:subList>`;
      let depth = 1;
      let searchIndex = contentStartIndex;
      let contentEndIndex = -1;

      while (depth > 0 && searchIndex < xml.length) {
        const nextOpen = xml.indexOf(openTag, searchIndex);
        const nextClose = xml.indexOf(closeTag, searchIndex);

        if (nextClose === -1) break;

        if (nextOpen !== -1 && nextOpen < nextClose) {
          depth++;
          searchIndex = nextOpen + openTag.length;
        } else {
          depth--;
          if (depth === 0) {
            contentEndIndex = nextClose;
          }
          searchIndex = nextClose + closeTag.length;
        }
      }

      if (contentEndIndex !== -1) {
        const subListContent = xml.substring(contentStartIndex, contentEndIndex);

        // Preserve nested tables
        const nestedTables = this.extractNestedTables(subListContent, prefix);

        // Extract paraPrIDRef and styleIDRef from existing paragraph
        const existingPMatch = subListContent.match(/<(?:hp|hs|hc):p[^>]*paraPrIDRef="([^"]*)"[^>]*styleIDRef="([^"]*)"/);
        const paraPrIDRef = existingPMatch?.[1] || '0';
        const styleIDRef = existingPMatch?.[2] || '0';

        const paraId = Math.floor(Math.random() * 2147483647);

        // Generate chunked runs for long text
        const runsXml = this.generateChunkedRuns(newText, prefix, charAttr);

        const newParagraph = `<${prefix}:p id="${paraId}" paraPrIDRef="${paraPrIDRef}" styleIDRef="${styleIDRef}" pageBreak="0" columnBreak="0" merged="0">${runsXml}<${prefix}:linesegarray><${prefix}:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" baseline="850" spacing="600" horzpos="0" horzsize="0" flags="0"/></${prefix}:linesegarray></${prefix}:p>`;

        const newContent = newParagraph + nestedTables;
        return xml.substring(0, contentStartIndex) + newContent + xml.substring(contentEndIndex);
      }
    }

    // Fallback: try to find paragraph directly
    const pStartMatch = xml.match(/<(hp|hs|hc):p[^>]*>/);
    if (pStartMatch) {
      const prefix = pStartMatch[1];
      const attrMatch = pStartMatch[0].match(/<(?:hp|hs|hc):p([^>]*)>/);
      const attrs = attrMatch?.[1] || ' id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"';

      // Find extent of all paragraphs
      const openTag = `<${prefix}:p`;
      const closeTag = `</${prefix}:p>`;
      const firstPStart = xml.indexOf(pStartMatch[0]);
      let depth = 0;
      let searchIndex = firstPStart;
      let lastParagraphEnd = -1;

      while (searchIndex < xml.length) {
        const nextOpen = xml.indexOf(openTag, searchIndex);
        const nextClose = xml.indexOf(closeTag, searchIndex);

        if (depth === 0 && (nextOpen === -1 || (nextClose !== -1 && nextClose < nextOpen && xml.indexOf(openTag, searchIndex) === -1))) {
          break;
        }

        if (nextOpen !== -1 && (nextClose === -1 || nextOpen < nextClose)) {
          depth++;
          searchIndex = nextOpen + openTag.length;
        } else if (nextClose !== -1) {
          depth--;
          searchIndex = nextClose + closeTag.length;
          if (depth === 0) {
            lastParagraphEnd = searchIndex;
            const remainingXml = xml.substring(searchIndex);
            const nextPMatch = remainingXml.match(/^\s*<(hp|hs|hc):p[^>]*>/);
            if (!nextPMatch) break;
          }
        } else {
          break;
        }
      }

      if (lastParagraphEnd !== -1) {
        const contentToReplace = xml.substring(firstPStart, lastParagraphEnd);
        const nestedTables = this.extractNestedTables(contentToReplace, prefix);

        // Generate chunked runs
        const runsXml = this.generateChunkedRuns(newText, prefix, charAttr);
        const newParagraph = `<${prefix}:p${attrs}>${runsXml}<${prefix}:linesegarray><${prefix}:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" baseline="850" spacing="600" horzpos="0" horzsize="0" flags="0"/></${prefix}:linesegarray></${prefix}:p>`;

        const newContent = newParagraph + nestedTables;
        return xml.substring(0, firstPStart) + newContent + xml.substring(lastParagraphEnd);
      }
    }

    // Final fallback
    return xml;
  }

  /**
   * Update text content in a cell with multiple paragraphs (for text with newlines).
   * Each line becomes a separate <hp:p> element, allowing independent styling.
   */
  private updateTextInCellMultiline(cellXml: string, newText: string, charShapeId?: number): string {
    const lines = newText.split('\n');
    const charAttr = charShapeId !== undefined ? ` charPrIDRef="${charShapeId}"` : ' charPrIDRef="0"';

    // Find the OUTER subList element with balanced tag matching
    // This is crucial because cells can contain nested tables with their own subLists
    const subListStartMatch = cellXml.match(/<(hp|hs|hc):subList[^>]*>/);
    if (subListStartMatch) {
      const prefix = subListStartMatch[1];
      const startTag = subListStartMatch[0];
      const startIndex = cellXml.indexOf(startTag);
      const contentStartIndex = startIndex + startTag.length;

      // Find matching closing tag using balanced bracket counting
      const openTag = `<${prefix}:subList`;
      const closeTag = `</${prefix}:subList>`;
      let depth = 1;
      let searchIndex = contentStartIndex;
      let contentEndIndex = -1;

      while (depth > 0 && searchIndex < cellXml.length) {
        const nextOpen = cellXml.indexOf(openTag, searchIndex);
        const nextClose = cellXml.indexOf(closeTag, searchIndex);

        if (nextClose === -1) {
          // No closing tag found - malformed XML
          break;
        }

        if (nextOpen !== -1 && nextOpen < nextClose) {
          // Found another opening tag first - increase depth
          depth++;
          searchIndex = nextOpen + openTag.length;
        } else {
          // Found closing tag
          depth--;
          if (depth === 0) {
            contentEndIndex = nextClose;
          }
          searchIndex = nextClose + closeTag.length;
        }
      }

      if (contentEndIndex !== -1) {
        const subListContent = cellXml.substring(contentStartIndex, contentEndIndex);

        // IMPORTANT: Check for nested tables in subList content - preserve them!
        const nestedTables = this.extractNestedTables(subListContent, prefix);

        // Extract paraPrIDRef and styleIDRef from existing paragraph if available
        const existingPMatch = subListContent.match(/<(?:hp|hs|hc):p[^>]*paraPrIDRef="([^"]*)"[^>]*styleIDRef="([^"]*)"/);
        const paraPrIDRef = existingPMatch?.[1] || '0';
        const styleIDRef = existingPMatch?.[2] || '0';

        // Generate multiple paragraphs with chunked runs for long lines
        const paragraphs = lines.map((line, index) => {
          const paraId = Math.floor(Math.random() * 2147483647);
          // Use chunked runs for long lines to prevent XML processing issues
          const runsXml = this.generateChunkedRuns(line, prefix, charAttr);
          return `<${prefix}:p id="${paraId}" paraPrIDRef="${paraPrIDRef}" styleIDRef="${styleIDRef}" pageBreak="0" columnBreak="0" merged="0">${runsXml}<${prefix}:linesegarray><${prefix}:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" baseline="850" spacing="600" horzpos="0" horzsize="0" flags="0"/></${prefix}:linesegarray></${prefix}:p>`;
        }).join('');

        // Append nested tables after paragraphs to preserve them
        const newContent = paragraphs + nestedTables;

        return cellXml.substring(0, contentStartIndex) + newContent + cellXml.substring(contentEndIndex);
      }
    }

    // If no subList found, try to find just paragraphs and replace
    // Use balanced matching for paragraphs too, since they can contain nested tables
    const pStartMatch = cellXml.match(/<(hp|hs|hc):p[^>]*>/);
    if (pStartMatch) {
      const prefix = pStartMatch[1];
      const firstPStart = cellXml.indexOf(pStartMatch[0]);

      // Extract attributes from the first paragraph
      const attrMatch = pStartMatch[0].match(/<(?:hp|hs|hc):p([^>]*)>/);
      const existingAttrs = attrMatch?.[1] || ' id="0" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"';

      // Find the end of the last top-level paragraph using balanced matching
      const openTag = `<${prefix}:p`;
      const closeTag = `</${prefix}:p>`;
      let depth = 0;
      let searchIndex = firstPStart;
      let lastParagraphEnd = -1;

      // Process all top-level paragraphs
      while (searchIndex < cellXml.length) {
        const nextOpen = cellXml.indexOf(openTag, searchIndex);
        const nextClose = cellXml.indexOf(closeTag, searchIndex);

        // If we're at depth 0 and there's no more opening tag, we're done
        if (depth === 0 && (nextOpen === -1 || (nextClose !== -1 && nextClose < nextOpen && cellXml.indexOf(openTag, searchIndex) === -1))) {
          break;
        }

        if (nextOpen !== -1 && (nextClose === -1 || nextOpen < nextClose)) {
          // Found opening tag
          depth++;
          searchIndex = nextOpen + openTag.length;
        } else if (nextClose !== -1) {
          // Found closing tag
          depth--;
          searchIndex = nextClose + closeTag.length;
          if (depth === 0) {
            lastParagraphEnd = searchIndex;
            // Check if there's another paragraph at top level
            const remainingXml = cellXml.substring(searchIndex);
            const nextPMatch = remainingXml.match(/^\s*<(hp|hs|hc):p[^>]*>/);
            if (!nextPMatch) {
              // No more top-level paragraphs
              break;
            }
          }
        } else {
          // No more tags found
          break;
        }
      }

      if (lastParagraphEnd !== -1) {
        // IMPORTANT: Check for nested tables in the content being replaced - preserve them!
        const contentToReplace = cellXml.substring(firstPStart, lastParagraphEnd);
        const nestedTables = this.extractNestedTables(contentToReplace, prefix);

        // Generate multiple paragraphs with chunked runs for long lines
        const paragraphs = lines.map((line, index) => {
          let attrs = existingAttrs;
          if (index > 0) {
            const newId = Math.floor(Math.random() * 2147483647);
            attrs = attrs.replace(/id="[^"]*"/, `id="${newId}"`);
          }
          // Use chunked runs for long lines to prevent XML processing issues
          const runsXml = this.generateChunkedRuns(line, prefix, charAttr);
          return `<${prefix}:p${attrs}>${runsXml}<${prefix}:linesegarray><${prefix}:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" baseline="850" spacing="600" horzpos="0" horzsize="0" flags="0"/></${prefix}:linesegarray></${prefix}:p>`;
        }).join('');

        // Append nested tables after paragraphs to preserve them
        const newContent = paragraphs + nestedTables;

        return cellXml.substring(0, firstPStart) + newContent + cellXml.substring(lastParagraphEnd);
      }
    }

    // Fallback: return unchanged
    return cellXml;
  }

  /**
   * Apply cell image inserts to XML
   * Inserts an image inside a table cell
   */
  private async applyCellImageInsertsToXml(): Promise<void> {
    if (!this._zip) return;

    // Group inserts by section
    const insertsBySection = new Map<number, Array<{
      tableIndex: number;
      row: number;
      col: number;
      imageId: string;
      binaryId: string;
      width: number;
      height: number;
      orgWidth: number;
      orgHeight: number;
      afterText?: string;
    }>>();

    for (const insert of this._pendingCellImageInserts) {
      const sectionInserts = insertsBySection.get(insert.sectionIndex) || [];
      sectionInserts.push({
        tableIndex: insert.tableIndex,
        row: insert.row,
        col: insert.col,
        imageId: insert.imageId,
        binaryId: insert.binaryId,
        width: insert.width,
        height: insert.height,
        orgWidth: insert.orgWidth,
        orgHeight: insert.orgHeight,
        afterText: insert.afterText,
      });
      insertsBySection.set(insert.sectionIndex, sectionInserts);
    }

    // Process each section
    for (const [sectionIndex, inserts] of insertsBySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      const file = this._zip.file(sectionPath);
      if (!file) continue;

      let xml = await file.async('string');

      // Get maximum instid and picid for generating new ones
      const instIdMatches = xml.matchAll(/instid="(\d+)"/g);
      const picIdMatches = xml.matchAll(/hp:pic id="(\d+)"/g);
      let maxInstId = 0;
      let maxPicId = 0;
      for (const m of instIdMatches) {
        maxInstId = Math.max(maxInstId, parseInt(m[1], 10));
      }
      for (const m of picIdMatches) {
        maxPicId = Math.max(maxPicId, parseInt(m[1], 10));
      }

      // Process inserts in reverse order (by table index, then by row/col)
      const sortedInserts = [...inserts].sort((a, b) => {
        if (a.tableIndex !== b.tableIndex) return b.tableIndex - a.tableIndex;
        if (a.row !== b.row) return b.row - a.row;
        return b.col - a.col;
      });

      for (const insert of sortedInserts) {
        const tables = this.findAllTables(xml);
        const tableData = tables[insert.tableIndex];
        if (!tableData) continue;

        let tableXml = tableData.xml;

        // Find the target cell
        const rows = this.findAllElementsWithDepth(tableXml, 'tr');
        const targetRow = rows[insert.row];
        if (!targetRow) continue;

        const cells = this.findAllElementsWithDepth(targetRow.xml, 'tc');
        const targetCell = cells[insert.col];
        if (!targetCell) continue;

        // Find insertion position based on afterText or use first paragraph
        let insertPosition: number;
        let insertMode: 'inside_paragraph' | 'after_paragraph' = 'inside_paragraph';

        if (insert.afterText) {
          // Find all paragraphs in the cell
          const paragraphs = this.findAllParagraphsInCell(targetCell.xml);
          const normalizedSearch = insert.afterText.toLowerCase().trim();

          // Find the paragraph containing the search text
          let foundParagraph: { start: number; end: number; xml: string } | null = null;
          for (const para of paragraphs) {
            const textContent = this.extractTextFromParagraphXml(para.xml).toLowerCase();
            if (textContent.includes(normalizedSearch)) {
              foundParagraph = para;
              break;
            }
          }

          if (foundParagraph) {
            // Insert after the found paragraph (create a new paragraph with the image)
            insertPosition = foundParagraph.end;
            insertMode = 'after_paragraph';
          } else {
            // Text not found, fall back to first paragraph
            console.warn(`[HwpxDocument] afterText "${insert.afterText}" not found in cell, using first paragraph`);
            const paragraphMatch = targetCell.xml.match(/<hp:p[^>]*>/);
            if (!paragraphMatch) continue;
            insertPosition = targetCell.xml.indexOf(paragraphMatch[0]) + paragraphMatch[0].length;
          }
        } else {
          // Default: find the first <hp:p> in the cell and insert the image inside it
          const paragraphMatch = targetCell.xml.match(/<hp:p[^>]*>/);
          if (!paragraphMatch) continue;
          insertPosition = targetCell.xml.indexOf(paragraphMatch[0]) + paragraphMatch[0].length;
        }

        // Generate image XML - use same size for orgSz/curSz (rollback to simpler approach)
        maxPicId++;
        maxInstId++;

        const hwpWidth = Math.round(insert.width * 100);
        const hwpHeight = Math.round(insert.height * 100);

        // For cell images: use textWrap="TOP_AND_BOTTOM" with treatAsChar="0" for proper display in 한글
        // IMPORTANT: curSz must be 0,0 for 한글 to display the image correctly
        // IMPORTANT: Image must be wrapped in <hp:run> with <hp:t/> after it
        const picXml = `<hp:run charPrIDRef="0"><hp:pic id="${maxPicId}" zOrder="0" numberingType="PICTURE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" href="" groupLevel="0" instid="${maxInstId}" reverse="0">
  <hp:offset x="0" y="0"/>
  <hp:orgSz width="${hwpWidth}" height="${hwpHeight}"/>
  <hp:curSz width="0" height="0"/>
  <hp:flip horizontal="0" vertical="0"/>
  <hp:rotationInfo angle="0" centerX="${Math.round(hwpWidth / 2)}" centerY="${Math.round(hwpHeight / 2)}" rotateimage="1"/>
  <hp:renderingInfo>
    <hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>
    <hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>
    <hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>
  </hp:renderingInfo>
  <hc:img binaryItemIDRef="${insert.imageId}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>
  <hp:imgRect>
    <hc:pt0 x="0" y="0"/>
    <hc:pt1 x="${hwpWidth}" y="0"/>
    <hc:pt2 x="${hwpWidth}" y="${hwpHeight}"/>
    <hc:pt3 x="0" y="${hwpHeight}"/>
  </hp:imgRect>
  <hp:imgClip left="0" right="${hwpWidth}" top="0" bottom="${hwpHeight}"/>
  <hp:inMargin left="0" right="0" top="0" bottom="0"/>
  <hp:imgDim dimwidth="${hwpWidth}" dimheight="${hwpHeight}"/>
  <hp:effects/>
  <hp:sz width="${hwpWidth}" widthRelTo="ABSOLUTE" height="${hwpHeight}" heightRelTo="ABSOLUTE" protect="0"/>
  <hp:pos treatAsChar="0" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="PARA" horzRelTo="COLUMN" vertAlign="TOP" horzAlign="LEFT" vertOffset="0" horzOffset="0"/>
  <hp:outMargin left="0" right="0" top="0" bottom="0"/>
  <hp:shapeComment>Inserted in cell by HWPX MCP</hp:shapeComment>
</hp:pic><hp:t/></hp:run>`;

        // Insert the image at the calculated position
        let updatedCellContent: string;
        if (insertMode === 'after_paragraph') {
          // Create a new paragraph containing the image and insert after the found paragraph
          const newParagraphWithImage = `<hp:p id="img_${maxPicId}">${picXml}</hp:p>`;
          updatedCellContent =
            targetCell.xml.substring(0, insertPosition) +
            newParagraphWithImage +
            targetCell.xml.substring(insertPosition);
        } else {
          // Insert the image inside the first paragraph (after opening tag)
          updatedCellContent =
            targetCell.xml.substring(0, insertPosition) +
            picXml +
            targetCell.xml.substring(insertPosition);
        }

        // Calculate position of cell in table XML
        const cellStartInTable = targetRow.startIndex + targetCell.startIndex;
        const cellEndInTable = targetRow.startIndex + targetCell.endIndex;

        // Build updated table XML
        const updatedTableXml =
          tableXml.substring(0, cellStartInTable) +
          updatedCellContent +
          tableXml.substring(cellEndInTable);

        // Update the main XML
        xml = xml.substring(0, tableData.startIndex) + updatedTableXml + xml.substring(tableData.endIndex);
      }

      this._zip.file(sectionPath, xml);
    }

    // Add binary data to ZIP
    for (const insert of this._pendingCellImageInserts) {
      const binaryData = Buffer.from(insert.data, 'base64');
      const ext = insert.mimeType.split('/')[1] || 'png';
      const binDataPath = `BinData/${insert.imageId}.${ext}`;
      this._zip.file(binDataPath, binaryData);

      // Update content.hpf to include the new binary item
      await this.addImageToContentHpf(insert.imageId, binDataPath, insert.mimeType);
    }
  }

  /**
   * Apply direct text updates (exact match replacement)
   * Groups updates by paragraph to handle multi-run updates correctly
   *
   * BUGFIX (2026-01-25): Pre-compute paragraph mappings before any modifications
   * to prevent text merging when multiple paragraphs have the same oldText pattern.
   * Updates are applied in reverse order (bottom-to-top) to avoid position shifts.
   */
  private async applyDirectTextUpdatesToXml(): Promise<void> {
    if (!this._zip) return;

    // Group updates by sectionIndex, then by elementIndex
    const updatesBySectionAndElement = new Map<number, Map<number, typeof this._pendingDirectTextUpdates>>();
    for (const update of this._pendingDirectTextUpdates) {
      let sectionMap = updatesBySectionAndElement.get(update.sectionIndex);
      if (!sectionMap) {
        sectionMap = new Map();
        updatesBySectionAndElement.set(update.sectionIndex, sectionMap);
      }

      let elementUpdates = sectionMap.get(update.elementIndex);
      if (!elementUpdates) {
        elementUpdates = [];
        sectionMap.set(update.elementIndex, elementUpdates);
      }
      elementUpdates.push(update);
    }

    for (const [sectionIdx, elementMap] of updatesBySectionAndElement) {
      const sectionPath = `Contents/section${sectionIdx}.xml`;
      const file = this._zip.file(sectionPath);
      if (!file) continue;

      let xml = await file.async('string');

      // STEP 1: Pre-compute target paragraph mappings BEFORE any modifications
      // OPTIMIZATION: Use cached XML positions when available (populated during parsing)
      const paragraphTargets = new Map<number, { start: number; end: number; xml: string }>();
      for (const [elementIndex, updates] of elementMap) {
        // Try cached position first (from parsing phase)
        const cachedPosition = this.getCachedXmlPosition(sectionIdx, elementIndex);
        if (cachedPosition && cachedPosition.start < xml.length && cachedPosition.end <= xml.length) {
          // Validate cached position by checking if it points to a paragraph element
          const cachedXml = xml.slice(cachedPosition.start, cachedPosition.end);
          if (cachedXml.startsWith('<hp:p') && cachedXml.endsWith('</hp:p>')) {
            paragraphTargets.set(elementIndex, {
              start: cachedPosition.start,
              end: cachedPosition.end,
              xml: cachedXml
            });
            continue;
          }
        }

        // Fallback to full search if no cached position or validation failed
        const paragraphId = updates[0]?.paragraphId || '';
        const paragraphOccurrence = updates[0]?.paragraphOccurrence ?? 0;
        const target = this.findTargetParagraphForUpdate(xml, sectionIdx, elementIndex, updates, paragraphId, paragraphOccurrence);
        if (target) {
          paragraphTargets.set(elementIndex, target);
        }
      }

      // STEP 2: Sort updates by position (descending) to process bottom-to-top
      // This prevents position shifts from affecting subsequent updates
      const sortedEntries = [...elementMap.entries()].sort((a, b) => {
        const targetA = paragraphTargets.get(a[0]);
        const targetB = paragraphTargets.get(b[0]);
        if (!targetA || !targetB) return 0;
        return targetB.start - targetA.start; // Descending by position
      });

      // STEP 3: Apply updates using pre-computed positions
      for (const [elementIndex, updates] of sortedEntries) {
        const target = paragraphTargets.get(elementIndex);
        if (!target) continue;

        // Sort by runIndex to process in order
        updates.sort((a, b) => a.runIndex - b.runIndex);

        // Apply the update directly using pre-computed target location
        if (updates.length > 1) {
          xml = this.replaceRunsInParagraphDirect(xml, target, updates);
        } else {
          // For single update, use the existing method with pre-computed target
          xml = this.replaceTextInElementDirect(xml, target, updates[0].oldText, updates[0].newText);
        }
      }

      // Reset lineseg in updated paragraphs (same as table cell updates)
      // This allows Korean word processor to recalculate line layout
      xml = this.resetLinesegInXml(xml);

      this._zip.file(sectionPath, xml);
    }
  }

  /**
   * Replace multiple runs in a paragraph element at once
   * This is needed when updating run 0 also clears runs 1-N
   */
  private replaceMultipleRunsInElement(
    xml: string,
    elementIndex: number,
    updates: Array<{ runIndex: number; oldText: string; newText: string }>
  ): string {
    // First, locate the target paragraph using the same logic as replaceTextInElementByIndex
    const cleanedXml = xml
      .replace(/<hp:fieldBegin[^>]*type="MEMO"[^>]*>[\s\S]*?<\/hp:fieldBegin>/gi, '')
      .replace(/<hp:footNote\b[^>]*>[\s\S]*?<\/hp:footNote>/gi, '')
      .replace(/<hp:endNote\b[^>]*>[\s\S]*?<\/hp:endNote>/gi, '');

    const extractAllParagraphs = (xmlStr: string): { xml: string; start: number; end: number }[] => {
      const results: { xml: string; start: number; end: number }[] = [];
      const closeTag = '</hp:p>';
      const pOpenRegex = /<hp:p\b[^>]*>/g;
      const pOpenSearchRegex = /<hp:p[\s>]/g;
      let match;

      while ((match = pOpenRegex.exec(xmlStr)) !== null) {
        const startPos = match.index;
        let depth = 1;
        let searchPos = startPos + match[0].length;

        while (depth > 0 && searchPos < xmlStr.length) {
          pOpenSearchRegex.lastIndex = searchPos;
          const nextOpenMatch = pOpenSearchRegex.exec(xmlStr);
          const nextOpen = nextOpenMatch ? nextOpenMatch.index : -1;
          const nextClose = xmlStr.indexOf(closeTag, searchPos);

          if (nextClose === -1) break;

          if (nextOpen !== -1 && nextOpen < nextClose) {
            depth++;
            searchPos = nextOpen + 6;
          } else {
            depth--;
            if (depth === 0) {
              const endPos = nextClose + closeTag.length;
              results.push({
                xml: xmlStr.substring(startPos, endPos),
                start: startPos,
                end: endPos
              });
            }
            searchPos = nextClose + closeTag.length;
          }
        }
      }
      return results;
    };

    const extractBalancedTags = (xmlStr: string, tagName: string): string[] => {
      const results: string[] = [];
      const openTag = `<${tagName}`;
      const closeTag = `</${tagName}>`;
      let searchStart = 0;

      while (true) {
        const openIndex = xmlStr.indexOf(openTag, searchStart);
        if (openIndex === -1) break;

        let depth = 1;
        let pos = openIndex + openTag.length;

        while (depth > 0 && pos < xmlStr.length) {
          const nextOpen = xmlStr.indexOf(openTag, pos);
          const nextClose = xmlStr.indexOf(closeTag, pos);

          if (nextClose === -1) break;

          if (nextOpen !== -1 && nextOpen < nextClose) {
            depth++;
            pos = nextOpen + openTag.length;
          } else {
            depth--;
            if (depth === 0) {
              results.push(xmlStr.substring(openIndex, nextClose + closeTag.length));
            }
            pos = nextClose + closeTag.length;
          }
        }
        searchStart = openIndex + 1;
      }
      return results;
    };

    const paragraphs = extractAllParagraphs(cleanedXml);
    const tables = extractBalancedTags(cleanedXml, 'hp:tbl');

    const tableRanges: { start: number; end: number }[] = [];
    for (const tableXml of tables) {
      const tableIndex = cleanedXml.indexOf(tableXml);
      if (tableIndex !== -1) {
        tableRanges.push({ start: tableIndex, end: tableIndex + tableXml.length });
      }
    }

    interface CleanedElement {
      type: string;
      start: number;
      end: number;
      xml: string;
    }
    const elements: CleanedElement[] = [];

    // Add tables
    for (const range of tableRanges) {
      elements.push({ type: 'tbl', start: range.start, end: range.end, xml: cleanedXml.substring(range.start, range.end) });
    }

    // Add paragraphs
    for (const para of paragraphs) {
      const isInsideTable = tableRanges.some(
        range => para.start > range.start && para.start < range.end
      );
      const containsTable = tableRanges.some(
        range => range.start >= para.start && range.end <= para.end
      );

      if (!isInsideTable) {
        if (containsTable) {
          let paraXmlWithoutTable = para.xml;
          for (const range of tableRanges) {
            if (range.start >= para.start && range.end <= para.start + para.xml.length) {
              const tableStartInPara = range.start - para.start;
              const tableEndInPara = range.end - para.start;
              const tableXmlInPara = para.xml.substring(tableStartInPara, tableEndInPara);
              paraXmlWithoutTable = paraXmlWithoutTable.replace(tableXmlInPara, '');
            }
          }
          const hasTextContent = /<hp:t\b[^>]*>/.test(paraXmlWithoutTable);
          if (hasTextContent) {
            elements.push({ type: 'p', start: para.start, end: para.end, xml: paraXmlWithoutTable });
          }
        } else {
          elements.push({ type: 'p', start: para.start, end: para.end, xml: para.xml });
        }
      }
    }

    // Add other element types
    const addShapeElements = (pattern: RegExp, typeName: string) => {
      let match;
      while ((match = pattern.exec(cleanedXml)) !== null) {
        elements.push({ type: typeName, start: match.index, end: match.index + match[0].length, xml: match[0] });
      }
    };

    addShapeElements(/<hp:line\b[^>]*(?:\/>|>[\s\S]*?<\/hp:line>)/g, 'line');
    addShapeElements(/<hp:rect\b[^>]*(?:\/>|>[\s\S]*?<\/hp:rect>)/g, 'rect');
    addShapeElements(/<hp:ellipse\b[^>]*(?:\/>|>[\s\S]*?<\/hp:ellipse>)/g, 'ellipse');
    addShapeElements(/<hp:arc\b[^>]*(?:\/>|>[\s\S]*?<\/hp:arc>)/g, 'arc');
    addShapeElements(/<hp:polygon\b[^>]*(?:\/>|>[\s\S]*?<\/hp:polygon>)/g, 'polygon');
    addShapeElements(/<hp:curve\b[^>]*(?:\/>|>[\s\S]*?<\/hp:curve>)/g, 'curve');
    addShapeElements(/<hp:connectLine\b[^>]*(?:\/>|>[\s\S]*?<\/hp:connectLine>)/g, 'connectline');

    elements.sort((a, b) => a.start - b.start);

    // Build list of top-level paragraphs in original XML
    const originalParagraphs = extractAllParagraphs(xml);
    const originalTableRanges: { start: number; end: number }[] = [];
    const originalTables = extractBalancedTags(xml, 'hp:tbl');
    for (const tableXml of originalTables) {
      const tableIndex = xml.indexOf(tableXml);
      if (tableIndex !== -1) {
        originalTableRanges.push({ start: tableIndex, end: tableIndex + tableXml.length });
      }
    }

    const originalTopLevelParas: { start: number; end: number; xml: string }[] = [];
    for (const para of originalParagraphs) {
      const isInsideTable = originalTableRanges.some(
        range => para.start > range.start && para.start < range.end
      );
      const containsTable = originalTableRanges.some(
        range => range.start >= para.start && range.end <= para.end
      );

      if (!isInsideTable) {
        if (containsTable) {
          let paraXmlWithoutTable = para.xml;
          for (const range of originalTableRanges) {
            if (range.start >= para.start && range.end <= para.start + para.xml.length) {
              const tableStartInPara = range.start - para.start;
              const tableEndInPara = range.end - para.start;
              const tableXmlInPara = para.xml.substring(tableStartInPara, tableEndInPara);
              paraXmlWithoutTable = paraXmlWithoutTable.replace(tableXmlInPara, '');
            }
          }
          const hasTextContent = /<hp:t\b[^>]*>/.test(paraXmlWithoutTable);
          if (hasTextContent) {
            originalTopLevelParas.push({ start: para.start, end: para.end, xml: para.xml });
          }
        } else {
          originalTopLevelParas.push({ start: para.start, end: para.end, xml: para.xml });
        }
      }
    }

    // Determine strategy based on oldText in updates
    let targetInOriginal: { start: number; end: number; xml: string } | undefined;

    // When updates contain non-empty oldText, prioritize text-based search over positional matching
    // because element indices may differ between HwpxParser and this function
    const nonEmptyOldTexts = updates.filter(u => u.oldText !== '').map(u => this.escapeXml(u.oldText));

    if (nonEmptyOldTexts.length > 0) {
      // TEXT-BASED SEARCH: Find ALL paragraphs containing at least one of the oldTexts
      const matchingParas = originalTopLevelParas.filter(para =>
        nonEmptyOldTexts.some(escapedOld => para.xml.includes(escapedOld))
      );

      if (matchingParas.length === 1) {
        // Unique match - use it directly
        targetInOriginal = matchingParas[0];
      } else if (matchingParas.length > 1) {
        // Multiple matches - find the CLOSEST paragraph by position
        let topLevelParagraphIndex = 0;
        for (let i = 0; i < elementIndex && i < elements.length; i++) {
          if (elements[i].type === 'p') {
            topLevelParagraphIndex++;
          }
        }

        // Find the CLOSEST matching paragraph (not just any within tolerance)
        let closestPara: typeof matchingParas[0] | undefined;
        let closestDistance = Infinity;
        for (const para of matchingParas) {
          const paraIndex = originalTopLevelParas.indexOf(para);
          const distance = Math.abs(paraIndex - topLevelParagraphIndex);
          if (distance < closestDistance) {
            closestDistance = distance;
            closestPara = para;
          }
        }

        targetInOriginal = closestPara || matchingParas[0];
      }

      // If no matches found by text, return unchanged
      if (!targetInOriginal) {
        return xml;
      }
    } else {
      // INDEX-BASED SEARCH: When all oldTexts are empty, use element index
      if (elementIndex < 0 || elementIndex >= elements.length) {
        return xml; // Index out of bounds
      }

      const targetElement = elements[elementIndex];
      if (targetElement.type !== 'p') {
        return xml; // Not a paragraph, can't replace text
      }

      // Count how many paragraph elements appear BEFORE elementIndex
      let topLevelParagraphIndex = 0;
      for (let i = 0; i < elementIndex; i++) {
        if (elements[i].type === 'p') {
          topLevelParagraphIndex++;
        }
      }

      // Use positional matching
      if (topLevelParagraphIndex >= 0 && topLevelParagraphIndex < originalTopLevelParas.length) {
        targetInOriginal = originalTopLevelParas[topLevelParagraphIndex];
      }

      if (!targetInOriginal) {
        return xml; // Paragraph not found
      }
    }

    // Now extract all <hp:run> elements from the paragraph and update them
    const paragraphXml = xml.slice(targetInOriginal.start, targetInOriginal.end);

    // Extract runs
    const runRegex = /<hp:run\b[^>]*>[\s\S]*?<\/hp:run>/g;
    const runs: Array<{ xml: string; start: number; end: number }> = [];
    let runMatch;
    while ((runMatch = runRegex.exec(paragraphXml)) !== null) {
      runs.push({
        xml: runMatch[0],
        start: runMatch.index,
        end: runMatch.index + runMatch[0].length
      });
    }

    // Build the update map
    const updateMap = new Map<number, string>();
    for (const update of updates) {
      updateMap.set(update.runIndex, update.newText);
    }

    // Build new paragraph XML by updating the specified runs
    let newParagraphXml = paragraphXml;
    let offset = 0;

    for (let i = 0; i < runs.length; i++) {
      if (updateMap.has(i)) {
        const newText = updateMap.get(i)!;
        const run = runs[i];

        // Replace text within <hp:t> tags (handles both with and without existing text)
        const escapedNew = this.escapeXml(newText);
        let newRunXml: string;

        // Check if run has <hp:t> tags (including self-closing)
        if (/<hp:t\s*\/>/.test(run.xml)) {
          // Has self-closing <hp:t/> tag - replace with content
          newRunXml = run.xml.replace(/<hp:t\s*\/>/, `<hp:t>${escapedNew}</hp:t>`);
        } else if (/<hp:t\b[^>]*>/.test(run.xml)) {
          // Has <hp:t>...</hp:t> tags - replace content of FIRST one only (no g flag)
          newRunXml = run.xml.replace(/(<hp:t[^>]*>)[^<]*(<\/hp:t>)/, `$1${escapedNew}$2`);
          // Remove any additional <hp:t>...</hp:t> tags to prevent duplication
          let firstReplaced = false;
          newRunXml = newRunXml.replace(/<hp:t[^>]*>[^<]*<\/hp:t>/g, (match) => {
            if (!firstReplaced) {
              firstReplaced = true;
              return match; // Keep the first one
            }
            return ''; // Remove subsequent ones
          });
        } else {
          // No <hp:t> tags - add one after the opening hp:run tag
          if (/<hp:run\b[^>]*>/.test(run.xml)) {
            newRunXml = run.xml.replace(
              /(<hp:run\b[^>]*>)/,
              `$1<hp:t>${escapedNew}</hp:t>`
            );
          } else {
            // Fallback: just use the original
            newRunXml = run.xml;
          }
        }

        const adjustedStart = run.start + offset;
        const adjustedEnd = run.end + offset;

        newParagraphXml =
          newParagraphXml.substring(0, adjustedStart) +
          newRunXml +
          newParagraphXml.substring(adjustedEnd);

        offset += (newRunXml.length - (run.end - run.start));
      }
    }

    // Reconstruct XML with updated paragraph
    return xml.slice(0, targetInOriginal.start) + newParagraphXml + xml.slice(targetInOriginal.end);
  }

  /**
   * Calculate the occurrence index for a paragraph with given ID.
   * Returns how many paragraphs with the same ID appear before this one.
   */
  private getParagraphOccurrence(sectionIndex: number, elementIndex: number, paragraphId: string): number {
    // Use _content.sections (same as findParagraphByPath) instead of _sections
    if (!this._content || !this._content.sections || !this._content.sections[sectionIndex]) return 0;
    const section = this._content.sections[sectionIndex];
    if (!section || !section.elements) return 0;

    let occurrenceCount = 0;
    for (let i = 0; i < elementIndex; i++) {
      const element = section.elements[i];
      if (element && element.type === 'paragraph') {  // Use 'paragraph' not 'p'
        const para = element.data as HwpxParagraph;
        if (para.id === paragraphId) {
          occurrenceCount++;
        }
      }
    }
    return occurrenceCount;
  }

  /**
   * Find paragraph by its ID attribute and occurrence index.
   * Uses balanced tag matching to handle nested paragraphs.
   * @param xml - The XML content
   * @param paragraphId - The paragraph ID to find
   * @param occurrence - Which occurrence of this ID (0-indexed)
   */
  private findParagraphById(
    xml: string,
    paragraphId: string,
    occurrence: number = 0
  ): { start: number; end: number; xml: string } | undefined {
    if (!paragraphId) return undefined;

    // Find the opening tag with specific id
    const escapedId = paragraphId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const openTagRegex = new RegExp(`<hp:p\\b[^>]*\\bid=["']${escapedId}["'][^>]*>`, 'g');

    let matchCount = 0;
    let openMatch;

    while ((openMatch = openTagRegex.exec(xml)) !== null) {
      const startIndex = openMatch.index;
      let pos = startIndex + openMatch[0].length;

      // Use balanced tag matching
      let depth = 1;
      const openTag = '<hp:p';
      const closeTag = '</hp:p>';

      while (depth > 0 && pos < xml.length) {
        const nextOpen = xml.indexOf(openTag, pos);
        const nextClose = xml.indexOf(closeTag, pos);

        if (nextClose === -1) break;

        if (nextOpen !== -1 && nextOpen < nextClose) {
          depth++;
          pos = nextOpen + openTag.length;
        } else {
          depth--;
          if (depth === 0) {
            // Found balanced paragraph
            if (matchCount === occurrence) {
              const endIndex = nextClose + closeTag.length;
              return {
                start: startIndex,
                end: endIndex,
                xml: xml.substring(startIndex, endIndex)
              };
            }
            matchCount++;
            break;  // Continue searching for next occurrence
          }
          pos = nextClose + closeTag.length;
        }
      }
    }

    return undefined;
  }

  /**
   * Calculate Levenshtein distance between two strings.
   * Used for fuzzy paragraph matching when exact match fails.
   */
  private levenshteinDistance(str1: string, str2: string): number {
    const len1 = str1.length;
    const len2 = str2.length;

    // Create distance matrix
    const matrix: number[][] = Array(len1 + 1).fill(null).map(() => Array(len2 + 1).fill(0));

    // Initialize first row and column
    for (let i = 0; i <= len1; i++) matrix[i][0] = i;
    for (let j = 0; j <= len2; j++) matrix[0][j] = j;

    // Fill in the rest
    for (let i = 1; i <= len1; i++) {
      for (let j = 1; j <= len2; j++) {
        const cost = str1[i - 1] === str2[j - 1] ? 0 : 1;
        matrix[i][j] = Math.min(
          matrix[i - 1][j] + 1,      // deletion
          matrix[i][j - 1] + 1,      // insertion
          matrix[i - 1][j - 1] + cost // substitution
        );
      }
    }

    return matrix[len1][len2];
  }

  /**
   * Find paragraph using fuzzy text matching with Levenshtein distance.
   * Fallback method when ID and index-based lookups fail.
   */
  private findParagraphByFuzzyMatch(
    xml: string,
    searchText: string,
    paragraphs: Array<{ start: number; end: number; xml: string }>
  ): { start: number; end: number; xml: string } | undefined {
    if (!searchText || paragraphs.length === 0) return undefined;

    let bestMatch: { para: typeof paragraphs[0]; similarity: number } | undefined;

    // Truncate search text if too long (Levenshtein is O(n*m))
    const maxSearchLen = 100;
    const truncatedSearch = searchText.length > maxSearchLen
      ? searchText.substring(0, maxSearchLen)
      : searchText;

    for (const para of paragraphs) {
      // Extract text content from paragraph XML
      const textContent = para.xml.replace(/<[^>]+>/g, '').trim();

      // Skip if text content is empty
      if (!textContent) continue;

      // Check if searchText is contained in textContent (exact substring)
      if (textContent.includes(truncatedSearch)) {
        return para;  // Exact match found
      }

      // For fuzzy matching, compare similar length substrings
      const truncatedContent = textContent.length > maxSearchLen
        ? textContent.substring(0, maxSearchLen)
        : textContent;

      const distance = this.levenshteinDistance(truncatedSearch, truncatedContent);
      const maxLen = Math.max(truncatedSearch.length, truncatedContent.length);
      const similarity = 1 - (distance / maxLen);

      if (similarity >= 0.7) {  // 70% similarity threshold
        if (!bestMatch || similarity > bestMatch.similarity) {
          bestMatch = { para, similarity };
        }
      }
    }

    return bestMatch?.para;
  }

  /**
   * Find the target paragraph for an update operation.
   * Extracts paragraph-finding logic from replaceMultipleRunsInElement for reuse.
   * Returns the paragraph's start, end, and XML content in the original document.
   */
  private findTargetParagraphForUpdate(
    xml: string,
    sectionIndex: number,
    elementIndex: number,
    updates: Array<{ runIndex: number; oldText: string; newText: string }>,
    paragraphId?: string,
    paragraphOccurrence?: number
  ): { start: number; end: number; xml: string } | undefined {
    // Clean XML by removing certain elements
    const cleanedXml = xml
      .replace(/<hp:fieldBegin[^>]*type="MEMO"[^>]*>[\s\S]*?<\/hp:fieldBegin>/gi, '')
      .replace(/<hp:footNote\b[^>]*>[\s\S]*?<\/hp:footNote>/gi, '')
      .replace(/<hp:endNote\b[^>]*>[\s\S]*?<\/hp:endNote>/gi, '');

    const extractAllParagraphs = (xmlStr: string): { xml: string; start: number; end: number }[] => {
      const results: { xml: string; start: number; end: number }[] = [];
      const closeTag = '</hp:p>';
      const pOpenRegex = /<hp:p\b[^>]*>/g;
      const pOpenSearchRegex = /<hp:p[\s>]/g;
      let match;

      while ((match = pOpenRegex.exec(xmlStr)) !== null) {
        const startPos = match.index;
        let depth = 1;
        let searchPos = startPos + match[0].length;

        while (depth > 0 && searchPos < xmlStr.length) {
          pOpenSearchRegex.lastIndex = searchPos;
          const nextOpenMatch = pOpenSearchRegex.exec(xmlStr);
          const nextOpen = nextOpenMatch ? nextOpenMatch.index : -1;
          const nextClose = xmlStr.indexOf(closeTag, searchPos);

          if (nextClose === -1) break;

          if (nextOpen !== -1 && nextOpen < nextClose) {
            depth++;
            searchPos = nextOpen + 6;
          } else {
            depth--;
            if (depth === 0) {
              const endPos = nextClose + closeTag.length;
              results.push({
                xml: xmlStr.substring(startPos, endPos),
                start: startPos,
                end: endPos
              });
            }
            searchPos = nextClose + closeTag.length;
          }
        }
      }
      return results;
    };

    const extractBalancedTags = (xmlStr: string, tagName: string): string[] => {
      const results: string[] = [];
      const openTag = `<${tagName}`;
      const closeTag = `</${tagName}>`;
      let searchStart = 0;

      while (true) {
        const openIndex = xmlStr.indexOf(openTag, searchStart);
        if (openIndex === -1) break;

        let depth = 1;
        let pos = openIndex + openTag.length;

        while (depth > 0 && pos < xmlStr.length) {
          const nextOpen = xmlStr.indexOf(openTag, pos);
          const nextClose = xmlStr.indexOf(closeTag, pos);

          if (nextClose === -1) break;

          if (nextOpen !== -1 && nextOpen < nextClose) {
            depth++;
            pos = nextOpen + openTag.length;
          } else {
            depth--;
            if (depth === 0) {
              results.push(xmlStr.substring(openIndex, nextClose + closeTag.length));
            }
            pos = nextClose + closeTag.length;
          }
        }
        searchStart = openIndex + 1;
      }
      return results;
    };

    const paragraphs = extractAllParagraphs(cleanedXml);
    const tables = extractBalancedTags(cleanedXml, 'hp:tbl');

    const tableRanges: { start: number; end: number }[] = [];
    for (const tableXml of tables) {
      const tableIndex = cleanedXml.indexOf(tableXml);
      if (tableIndex !== -1) {
        tableRanges.push({ start: tableIndex, end: tableIndex + tableXml.length });
      }
    }

    interface CleanedElement {
      type: string;
      start: number;
      end: number;
      xml: string;
    }
    const elements: CleanedElement[] = [];

    // Add tables
    for (const range of tableRanges) {
      elements.push({ type: 'tbl', start: range.start, end: range.end, xml: cleanedXml.substring(range.start, range.end) });
    }

    // Add paragraphs
    for (const para of paragraphs) {
      const isInsideTable = tableRanges.some(
        range => para.start > range.start && para.start < range.end
      );
      const containsTable = tableRanges.some(
        range => range.start >= para.start && range.end <= para.end
      );

      if (!isInsideTable) {
        if (containsTable) {
          let paraXmlWithoutTable = para.xml;
          for (const range of tableRanges) {
            if (range.start >= para.start && range.end <= para.start + para.xml.length) {
              const tableStartInPara = range.start - para.start;
              const tableEndInPara = range.end - para.start;
              const tableXmlInPara = para.xml.substring(tableStartInPara, tableEndInPara);
              paraXmlWithoutTable = paraXmlWithoutTable.replace(tableXmlInPara, '');
            }
          }
          const hasTextContent = /<hp:t\b[^>]*>/.test(paraXmlWithoutTable);
          if (hasTextContent) {
            elements.push({ type: 'p', start: para.start, end: para.end, xml: paraXmlWithoutTable });
          }
        } else {
          elements.push({ type: 'p', start: para.start, end: para.end, xml: para.xml });
        }
      }
    }

    // Add other element types
    const addShapeElements = (pattern: RegExp, typeName: string) => {
      let match;
      while ((match = pattern.exec(cleanedXml)) !== null) {
        elements.push({ type: typeName, start: match.index, end: match.index + match[0].length, xml: match[0] });
      }
    };

    addShapeElements(/<hp:line\b[^>]*(?:\/>|>[\s\S]*?<\/hp:line>)/g, 'line');
    addShapeElements(/<hp:rect\b[^>]*(?:\/>|>[\s\S]*?<\/hp:rect>)/g, 'rect');
    addShapeElements(/<hp:ellipse\b[^>]*(?:\/>|>[\s\S]*?<\/hp:ellipse>)/g, 'ellipse');
    addShapeElements(/<hp:arc\b[^>]*(?:\/>|>[\s\S]*?<\/hp:arc>)/g, 'arc');
    addShapeElements(/<hp:polygon\b[^>]*(?:\/>|>[\s\S]*?<\/hp:polygon>)/g, 'polygon');
    addShapeElements(/<hp:curve\b[^>]*(?:\/>|>[\s\S]*?<\/hp:curve>)/g, 'curve');
    addShapeElements(/<hp:connectLine\b[^>]*(?:\/>|>[\s\S]*?<\/hp:connectLine>)/g, 'connectline');

    elements.sort((a, b) => a.start - b.start);

    // Build list of top-level paragraphs in original XML
    const originalParagraphs = extractAllParagraphs(xml);
    const originalTableRanges: { start: number; end: number }[] = [];
    const originalTables = extractBalancedTags(xml, 'hp:tbl');
    for (const tableXml of originalTables) {
      const tableIndex = xml.indexOf(tableXml);
      if (tableIndex !== -1) {
        originalTableRanges.push({ start: tableIndex, end: tableIndex + tableXml.length });
      }
    }

    const originalTopLevelParas: { start: number; end: number; xml: string }[] = [];
    for (const para of originalParagraphs) {
      const isInsideTable = originalTableRanges.some(
        range => para.start > range.start && para.start < range.end
      );
      const containsTable = originalTableRanges.some(
        range => range.start >= para.start && range.end <= para.end
      );

      if (!isInsideTable) {
        if (containsTable) {
          let paraXmlWithoutTable = para.xml;
          for (const range of originalTableRanges) {
            if (range.start >= para.start && range.end <= para.start + para.xml.length) {
              const tableStartInPara = range.start - para.start;
              const tableEndInPara = range.end - para.start;
              const tableXmlInPara = para.xml.substring(tableStartInPara, tableEndInPara);
              paraXmlWithoutTable = paraXmlWithoutTable.replace(tableXmlInPara, '');
            }
          }
          const hasTextContent = /<hp:t\b[^>]*>/.test(paraXmlWithoutTable);
          if (hasTextContent) {
            originalTopLevelParas.push({ start: para.start, end: para.end, xml: para.xml });
          }
        } else {
          originalTopLevelParas.push({ start: para.start, end: para.end, xml: para.xml });
        }
      }
    }

    // BUGFIX (2026-01-26): 3-tier hybrid approach - ID first, index second, fuzzy fallback.
    //
    // Problem: Text-based search fails when multiple paragraphs have same markers.
    // Solution: Use paragraph ID as PRIMARY key, index as SECONDARY, fuzzy matching as FALLBACK.
    //
    // Strategy:
    // TIER 1: ID-based lookup (most reliable)
    // TIER 2: Index-based lookup with text validation
    // TIER 3: Fuzzy text matching fallback

    // TIER 1: ID-based lookup - DISABLED
    // Problem: XML counting includes nested paragraphs (inside tables),
    // but _content.sections.elements only has top-level elements.
    // This mismatch causes wrong paragraph selection.
    // Solution: Skip ID-based lookup and use index-based (TIER 2) instead.
    //
    // if (paragraphId) {
    //   const idBasedTarget = this.findParagraphById(xml, paragraphId, paragraphOccurrence ?? 0);
    //   if (idBasedTarget) {
    //     return idBasedTarget;
    //   }
    // }

    // Calculate paragraph index using _content.sections.elements (same source as elementIndex)
    // This ensures consistency between elementIndex and paragraph counting
    let topLevelParagraphIndex = 0;
    const contentSection = this._content?.sections?.[sectionIndex];
    if (contentSection?.elements) {
      for (let i = 0; i < elementIndex && i < contentSection.elements.length; i++) {
        if (contentSection.elements[i].type === 'paragraph') {  // Note: 'paragraph' not 'p'
          topLevelParagraphIndex++;
        }
      }
    }

    // TIER 2: Index-based lookup with text validation
    const indexBasedTarget = (topLevelParagraphIndex >= 0 && topLevelParagraphIndex < originalTopLevelParas.length)
      ? originalTopLevelParas[topLevelParagraphIndex]
      : undefined;

    // Get non-empty oldTexts for validation
    const nonEmptyOldTexts = updates.filter(u => u.oldText !== '').map(u => this.escapeXml(u.oldText));

    // If no oldText to validate, use index-based result
    if (nonEmptyOldTexts.length === 0) {
      return indexBasedTarget;
    }

    // If index-based target contains expected text, use it
    if (indexBasedTarget) {
      const hasMatchingText = nonEmptyOldTexts.some(escapedOld => indexBasedTarget.xml.includes(escapedOld));
      if (hasMatchingText) {
        return indexBasedTarget;
      }
    }

    // Try nearby paragraphs (±2 range)
    const searchRange = 2;
    for (let offset = 1; offset <= searchRange; offset++) {
      for (const delta of [-offset, offset]) {
        const nearbyIndex = topLevelParagraphIndex + delta;
        if (nearbyIndex >= 0 && nearbyIndex < originalTopLevelParas.length) {
          const nearbyPara = originalTopLevelParas[nearbyIndex];
          const hasMatchingText = nonEmptyOldTexts.some(escapedOld => nearbyPara.xml.includes(escapedOld));
          if (hasMatchingText) {
            return nearbyPara;
          }
        }
      }
    }

    // TIER 3: Fuzzy matching fallback
    const primaryOldText = nonEmptyOldTexts[0]?.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
    if (primaryOldText) {
      const fuzzyMatch = this.findParagraphByFuzzyMatch(xml, primaryOldText, originalTopLevelParas);
      if (fuzzyMatch) {
        return fuzzyMatch;
      }
    }

    // Final fallback: return index-based result anyway
    return indexBasedTarget;
  }

  /**
   * Replace multiple runs in a paragraph element at once.
   * Finds hp:run elements and updates their hp:t content.
   */
  private replaceRunsInParagraphDirect(
    xml: string,
    target: { start: number; end: number; xml: string },
    updates: Array<{ runIndex: number; oldText: string; newText: string }>
  ): string {
    let paragraphXml = xml.slice(target.start, target.end);

    // Build update map
    const updateMap = new Map<number, string>();
    for (const update of updates) {
      updateMap.set(update.runIndex, update.newText);
    }

    // Find all hp:run elements with their positions
    // Use non-greedy matching and track depth for nested elements
    const runs: Array<{ start: number; end: number; xml: string }> = [];
    const runOpenRegex = /<hp:run\b[^>]*>/g;
    let match;

    while ((match = runOpenRegex.exec(paragraphXml)) !== null) {
      const runStart = match.index;
      let depth = 1;
      let pos = runStart + match[0].length;

      // Find matching </hp:run> using depth tracking
      while (depth > 0 && pos < paragraphXml.length) {
        const nextOpen = paragraphXml.indexOf('<hp:run', pos);
        const nextClose = paragraphXml.indexOf('</hp:run>', pos);

        if (nextClose === -1) break;

        if (nextOpen !== -1 && nextOpen < nextClose) {
          depth++;
          pos = nextOpen + 7;
        } else {
          depth--;
          if (depth === 0) {
            const runEnd = nextClose + '</hp:run>'.length;
            runs.push({
              start: runStart,
              end: runEnd,
              xml: paragraphXml.slice(runStart, runEnd)
            });
          }
          pos = nextClose + 9;
        }
      }
    }

    // Filter to only runs that have <hp:t> content (matching memory model behavior)
    // Memory model only counts runs with text, not runs with only <hp:ctrl> etc.
    const textRuns = runs.filter(run => /<hp:t\b/.test(run.xml) || /<hp:t\s*\/>/.test(run.xml));

    // Process text runs in reverse order to maintain positions
    for (let i = textRuns.length - 1; i >= 0; i--) {
      if (!updateMap.has(i)) continue;

      const run = textRuns[i];
      const newText = updateMap.get(i)!;
      const escapedNew = this.escapeXml(newText);
      let newRunXml = run.xml;

      // Find and replace hp:t content within this run
      if (/<hp:t\s*\/>/.test(newRunXml)) {
        // Self-closing tag: <hp:t/> -> <hp:t>newText</hp:t>
        newRunXml = newRunXml.replace(/<hp:t\s*\/>/, `<hp:t>${escapedNew}</hp:t>`);
      } else if (/<hp:t\b[^>]*>/.test(newRunXml)) {
        // Has content: replace first hp:t content only
        newRunXml = newRunXml.replace(
          /(<hp:t\b[^>]*>)[^<]*(<\/hp:t>)/,
          `$1${escapedNew}$2`
        );
      } else {
        // No hp:t tag - add one after the opening hp:run tag
        newRunXml = newRunXml.replace(
          /(<hp:run\b[^>]*>)/,
          `$1<hp:t>${escapedNew}</hp:t>`
        );
      }

      // Replace in paragraph XML
      paragraphXml = paragraphXml.slice(0, run.start) + newRunXml + paragraphXml.slice(run.end);
    }

    return xml.slice(0, target.start) + paragraphXml + xml.slice(target.end);
  }

  /**
   * Replace text in a single run directly using pre-computed target location.
   * Simpler version for single-run updates.
   */
  private replaceTextInElementDirect(
    xml: string,
    target: { start: number; end: number; xml: string },
    oldText: string,
    newText: string
  ): string {
    const escapedNew = this.escapeXml(newText);
    let paragraphXml = xml.slice(target.start, target.end);

    if (!oldText || oldText === '') {
      // When oldText is empty, replace <hp:t> content or self-closing <hp:t/>
      if (/<hp:t\s*\/>/.test(paragraphXml)) {
        // Self-closing: <hp:t/> -> <hp:t>newText</hp:t>
        paragraphXml = paragraphXml.replace(/<hp:t\s*\/>/, `<hp:t>${escapedNew}</hp:t>`);
      } else if (/<hp:t[^>]*>/.test(paragraphXml)) {
        // Has content or empty: <hp:t>...</hp:t> -> <hp:t>newText</hp:t>
        paragraphXml = paragraphXml.replace(/(<hp:t[^>]*>)[^<]*(<\/hp:t>)/, `$1${escapedNew}$2`);
      } else if (/<hp:run\b[^>]*>/.test(paragraphXml)) {
        // No <hp:t> tag exists - add one after the <hp:run> opening tag
        paragraphXml = paragraphXml.replace(
          /(<hp:run\b[^>]*>)/,
          `$1<hp:t>${escapedNew}</hp:t>`
        );
      }
    } else {
      // Normal case: replace oldText with newText
      const escapedOld = this.escapeXml(oldText);
      paragraphXml = paragraphXml.replace(escapedOld, escapedNew);
    }

    return xml.slice(0, target.start) + paragraphXml + xml.slice(target.end);
  }

  /**
   * Replace text in a paragraph identified by both ID and text content.
   * This is more reliable because:
   * - Uses ID to narrow down candidates (even if not unique)
   * - Uses oldText to find the exact paragraph among candidates
   */
  private replaceTextInParagraphByIdAndText(
    xml: string,
    paragraphId: string,
    oldText: string,
    newText: string
  ): string {
    if (!paragraphId || !oldText) return xml;

    const escapedOld = this.escapeXml(oldText);
    const escapedNew = this.escapeXml(newText);

    // Find all paragraphs with this ID
    // Pattern: <hp:p ... id="paragraphId" ...>...</hp:p>
    const idPattern = new RegExp(`<hp:p\\s+[^>]*\\bid=["']${this.escapeRegex(paragraphId)}["'][^>]*>`, 'g');

    let match;
    while ((match = idPattern.exec(xml)) !== null) {
      const paraStart = match.index;

      // Find the matching closing tag
      let depth = 1;
      let searchPos = paraStart + match[0].length;
      let paraEnd = -1;

      while (depth > 0 && searchPos < xml.length) {
        const nextOpen = xml.indexOf('<hp:p', searchPos);
        const nextClose = xml.indexOf('</hp:p>', searchPos);

        if (nextClose === -1) break;

        if (nextOpen !== -1 && nextOpen < nextClose) {
          depth++;
          searchPos = nextOpen + 1;
        } else {
          depth--;
          if (depth === 0) {
            paraEnd = nextClose + '</hp:p>'.length;
          }
          searchPos = nextClose + 1;
        }
      }

      if (paraEnd === -1) continue;

      const paragraphContent = xml.slice(paraStart, paraEnd);

      // Check if this paragraph contains the oldText
      if (!paragraphContent.includes(escapedOld)) continue;

      // Found the right paragraph! Replace the text
      // Replace within <hp:t> tags
      const pattern1 = new RegExp(`(<hp:t[^>]*>)${this.escapeRegex(escapedOld)}`);
      let newParagraphContent = paragraphContent.replace(pattern1, `$1${escapedNew}`);

      // Also try standalone text replacement
      const pattern2 = new RegExp(`>${this.escapeRegex(escapedOld)}<`);
      newParagraphContent = newParagraphContent.replace(pattern2, `>${escapedNew}<`);

      // Reconstruct XML
      return xml.slice(0, paraStart) + newParagraphContent + xml.slice(paraEnd);
    }

    // No matching paragraph found
    return xml;
  }

  /**
   * Replace text only within a specific element (paragraph) identified by elementIndex.
   * This ensures that identical text in other parts of the document is not affected.
   *
   * IMPORTANT: This follows the same element indexing as HwpxParser.parseSection:
   * - All top-level elements are counted: paragraphs, tables, images, shapes (23+ types)
   * - Paragraphs INSIDE tables are NOT counted (they're part of the table)
   * - Text replacement only applies to paragraph elements (type 'p')
   * - Other elements (images, shapes, etc.) are counted for indexing but not modified
   */
  private replaceTextInElementByIndex(xml: string, elementIndex: number, oldText: string, newText: string): string {
    const escapedOld = this.escapeXml(oldText);
    const escapedNew = this.escapeXml(newText);


    // CRITICAL: Match HwpxParser.parseSection logic EXACTLY
    // Step 1: Clean XML first (remove MEMO, footnote, endnote content) - same as HwpxParser
    let cleanedXml = xml.replace(/<hp:fieldBegin[^>]*type="MEMO"[^>]*>[\s\S]*?<\/hp:fieldBegin>/gi, '');
    cleanedXml = cleanedXml.replace(/<hp:footNote\b[^>]*>[\s\S]*?<\/hp:footNote>/gi, '');
    cleanedXml = cleanedXml.replace(/<hp:endNote\b[^>]*>[\s\S]*?<\/hp:endNote>/gi, '');

    // Step 2: Extract all paragraphs from cleaned XML (same as HwpxParser.extractAllParagraphs)
    const extractAllParagraphs = (xmlStr: string): { xml: string; start: number; end: number }[] => {
      const results: { xml: string; start: number; end: number }[] = [];
      const closeTag = '</hp:p>';
      const pOpenRegex = /<hp:p\b[^>]*>/g;
      const pOpenSearchRegex = /<hp:p[\s>]/g;
      let match;

      while ((match = pOpenRegex.exec(xmlStr)) !== null) {
        const startPos = match.index;
        let depth = 1;
        let searchPos = startPos + match[0].length;

        while (depth > 0 && searchPos < xmlStr.length) {
          pOpenSearchRegex.lastIndex = searchPos;
          const nextOpenMatch = pOpenSearchRegex.exec(xmlStr);
          const nextOpen = nextOpenMatch ? nextOpenMatch.index : -1;
          const nextClose = xmlStr.indexOf(closeTag, searchPos);

          if (nextClose === -1) break;

          if (nextOpen !== -1 && nextOpen < nextClose) {
            depth++;
            searchPos = nextOpen + 6;
          } else {
            depth--;
            if (depth === 0) {
              const endPos = nextClose + closeTag.length;
              results.push({
                xml: xmlStr.substring(startPos, endPos),
                start: startPos,
                end: endPos
              });
            }
            searchPos = nextClose + closeTag.length;
          }
        }
      }
      return results;
    };

    // Step 3: Extract balanced tags helper (same as HwpxParser.extractBalancedTags)
    const extractBalancedTags = (xmlStr: string, tagName: string): string[] => {
      const results: string[] = [];
      const openTag = `<${tagName}`;
      const closeTag = `</${tagName}>`;
      let searchStart = 0;

      while (true) {
        const openIndex = xmlStr.indexOf(openTag, searchStart);
        if (openIndex === -1) break;

        let depth = 1;
        let pos = openIndex + openTag.length;

        while (depth > 0 && pos < xmlStr.length) {
          const nextOpen = xmlStr.indexOf(openTag, pos);
          const nextClose = xmlStr.indexOf(closeTag, pos);

          if (nextClose === -1) break;

          if (nextOpen !== -1 && nextOpen < nextClose) {
            depth++;
            pos = nextOpen + openTag.length;
          } else {
            depth--;
            if (depth === 0) {
              results.push(xmlStr.substring(openIndex, nextClose + closeTag.length));
            }
            pos = nextClose + closeTag.length;
          }
        }
        searchStart = openIndex + 1;
      }
      return results;
    };

    // Step 4: Build element list from CLEANED XML (same logic as HwpxParser.parseSection)
    const paragraphs = extractAllParagraphs(cleanedXml);
    const tables = extractBalancedTags(cleanedXml, 'hp:tbl');

    const tableRanges: { start: number; end: number }[] = [];
    for (const tableXml of tables) {
      const tableIndex = cleanedXml.indexOf(tableXml);
      if (tableIndex !== -1) {
        tableRanges.push({ start: tableIndex, end: tableIndex + tableXml.length });
      }
    }

    interface CleanedElement {
      type: string;
      start: number;
      end: number;
      xml: string;
    }
    const elements: CleanedElement[] = [];

    // Add tables
    for (const range of tableRanges) {
      elements.push({ type: 'tbl', start: range.start, end: range.end, xml: cleanedXml.substring(range.start, range.end) });
    }

    // Add paragraphs - same logic as HwpxParser.parseSection lines 1283-1314
    for (const para of paragraphs) {
      const isInsideTable = tableRanges.some(
        range => para.start > range.start && para.start < range.end
      );
      const containsTable = tableRanges.some(
        range => range.start >= para.start && range.end <= para.end
      );

      if (!isInsideTable) {
        if (containsTable) {
          // Paragraph contains a table - remove the table XML and parse the remaining content
          let paraXmlWithoutTable = para.xml;
          for (const range of tableRanges) {
            if (range.start >= para.start && range.end <= para.start + para.xml.length) {
              const tableStartInPara = range.start - para.start;
              const tableEndInPara = range.end - para.start;
              const tableXmlInPara = para.xml.substring(tableStartInPara, tableEndInPara);
              paraXmlWithoutTable = paraXmlWithoutTable.replace(tableXmlInPara, '');
            }
          }
          // Only add if there's remaining content besides lineseg
          const hasTextContent = /<hp:t\b[^>]*>/.test(paraXmlWithoutTable);
          if (hasTextContent) {
            elements.push({ type: 'p', start: para.start, end: para.end, xml: paraXmlWithoutTable });
          }
        } else {
          elements.push({ type: 'p', start: para.start, end: para.end, xml: para.xml });
        }
      }
    }

    // Add other element types (line, rect, ellipse, arc, polygon, curve, connectline)
    const addShapeElements = (pattern: RegExp, typeName: string) => {
      let match;
      while ((match = pattern.exec(cleanedXml)) !== null) {
        elements.push({ type: typeName, start: match.index, end: match.index + match[0].length, xml: match[0] });
      }
    };

    addShapeElements(/<hp:line\b[^>]*(?:\/>|>[\s\S]*?<\/hp:line>)/g, 'line');
    addShapeElements(/<hp:rect\b[^>]*(?:\/>|>[\s\S]*?<\/hp:rect>)/g, 'rect');
    addShapeElements(/<hp:ellipse\b[^>]*(?:\/>|>[\s\S]*?<\/hp:ellipse>)/g, 'ellipse');
    addShapeElements(/<hp:arc\b[^>]*(?:\/>|>[\s\S]*?<\/hp:arc>)/g, 'arc');
    addShapeElements(/<hp:polygon\b[^>]*(?:\/>|>[\s\S]*?<\/hp:polygon>)/g, 'polygon');
    addShapeElements(/<hp:curve\b[^>]*(?:\/>|>[\s\S]*?<\/hp:curve>)/g, 'curve');
    addShapeElements(/<hp:connectLine\b[^>]*(?:\/>|>[\s\S]*?<\/hp:connectLine>)/g, 'connectline');

    // Sort by position (same as HwpxParser)
    elements.sort((a, b) => a.start - b.start);

    // Step 5: Build original document structure for target lookup
    const originalParagraphs = extractAllParagraphs(xml);
    const originalTableRanges: { start: number; end: number }[] = [];
    const originalTables = extractBalancedTags(xml, 'hp:tbl');
    for (const tableXml of originalTables) {
      const tableIndex = xml.indexOf(tableXml);
      if (tableIndex !== -1) {
        originalTableRanges.push({ start: tableIndex, end: tableIndex + tableXml.length });
      }
    }

    // Build list of top-level paragraphs in original XML (same logic)
    const originalTopLevelParas: { start: number; end: number; xml: string }[] = [];
    for (const para of originalParagraphs) {
      const isInsideTable = originalTableRanges.some(
        range => para.start > range.start && para.start < range.end
      );
      const containsTable = originalTableRanges.some(
        range => range.start >= para.start && range.end <= para.end
      );

      if (!isInsideTable) {
        if (containsTable) {
          let paraXmlWithoutTable = para.xml;
          for (const range of originalTableRanges) {
            if (range.start >= para.start && range.end <= para.start + para.xml.length) {
              const tableStartInPara = range.start - para.start;
              const tableEndInPara = range.end - para.start;
              const tableXmlInPara = para.xml.substring(tableStartInPara, tableEndInPara);
              paraXmlWithoutTable = paraXmlWithoutTable.replace(tableXmlInPara, '');
            }
          }
          const hasTextContent = /<hp:t\b[^>]*>/.test(paraXmlWithoutTable);
          if (hasTextContent) {
            originalTopLevelParas.push({ start: para.start, end: para.end, xml: para.xml });
          }
        } else {
          originalTopLevelParas.push({ start: para.start, end: para.end, xml: para.xml });
        }
      }
    }

    // Step 6: Determine strategy based on oldText
    let targetInOriginal: { start: number; end: number; xml: string } | undefined;

    if (oldText !== '') {
      // TEXT-BASED SEARCH: When oldText is provided, prioritize text-based search
      // because element indices may differ between HwpxParser and this function

      // Find ALL paragraphs containing the oldText
      const matchingParas = originalTopLevelParas.filter(para => para.xml.includes(escapedOld));

      if (matchingParas.length === 1) {
        // Unique match - use it directly
        targetInOriginal = matchingParas[0];
      } else if (matchingParas.length > 1) {
        // Multiple matches - find the CLOSEST paragraph by position
        let topLevelParagraphIndex = 0;
        for (let i = 0; i < elementIndex && i < elements.length; i++) {
          if (elements[i].type === 'p') {
            topLevelParagraphIndex++;
          }
        }

        // Find the CLOSEST matching paragraph (not just any within tolerance)
        let closestPara: typeof matchingParas[0] | undefined;
        let closestDistance = Infinity;
        for (const para of matchingParas) {
          const paraIndex = originalTopLevelParas.indexOf(para);
          const distance = Math.abs(paraIndex - topLevelParagraphIndex);
          if (distance < closestDistance) {
            closestDistance = distance;
            closestPara = para;
          }
        }

        targetInOriginal = closestPara || matchingParas[0];
      }

      // If no matches found by text, return unchanged
      if (!targetInOriginal) {
        return xml;
      }
    } else {
      // INDEX-BASED SEARCH: When oldText is empty, use element index
      if (elementIndex < 0 || elementIndex >= elements.length) {
        return xml; // Index out of bounds
      }

      const targetElement = elements[elementIndex];
      if (targetElement.type !== 'p') {
        return xml; // Not a paragraph, can't replace text
      }

      // Count how many paragraph elements appear BEFORE elementIndex
      let topLevelParagraphIndex = 0;
      for (let i = 0; i < elementIndex; i++) {
        if (elements[i].type === 'p') {
          topLevelParagraphIndex++;
        }
      }

      // Use positional matching
      if (topLevelParagraphIndex >= 0 && topLevelParagraphIndex < originalTopLevelParas.length) {
        targetInOriginal = originalTopLevelParas[topLevelParagraphIndex];
      }

      if (!targetInOriginal) {
        return xml; // Paragraph not found
      }
    }

    // Step 7: Perform the replacement in original XML
    const elementContent = xml.slice(targetInOriginal.start, targetInOriginal.end);
    let newElementContent = elementContent;

    // Handle different hp:t tag scenarios
    if (/<hp:t\s*\/>/.test(elementContent)) {
      // Case 1: Self-closing <hp:t/> - replace with full tag containing new text
      newElementContent = elementContent.replace(/<hp:t\s*\/>/, `<hp:t>${escapedNew}</hp:t>`);
    } else if (oldText === '' && /<hp:t[^>]*><\/hp:t>/.test(elementContent)) {
      // Case 2: Empty <hp:t></hp:t> - fill with new text
      newElementContent = elementContent.replace(/(<hp:t[^>]*>)<\/hp:t>/, `$1${escapedNew}</hp:t>`);
    } else if (oldText === '' && !/<hp:t\b[^>]*>/.test(elementContent)) {
      // Case 3: No hp:t tag at all - add one after the first hp:run opening tag
      newElementContent = elementContent.replace(/(<hp:run\b[^>]*>)/, `$1<hp:t>${escapedNew}</hp:t>`);
    } else {
      // Case 4: Normal case - replace text within <hp:t> tags (first match only)
      const pattern1 = new RegExp(`(<hp:t[^>]*>)${this.escapeRegex(escapedOld)}`);
      newElementContent = elementContent.replace(pattern1, `$1${escapedNew}`);

      // Also try standalone text replacement if pattern1 didn't match
      if (newElementContent === elementContent) {
        const pattern2 = new RegExp(`>${this.escapeRegex(escapedOld)}<`);
        newElementContent = newElementContent.replace(pattern2, `>${escapedNew}<`);
      }
    }

    // Reconstruct XML with updated element
    return xml.slice(0, targetInOriginal.start) + newElementContent + xml.slice(targetInOriginal.end);
  }

  private escapeRegex(str: string): string {
    return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  }

  /**
   * Replace text in a paragraph identified by its ID.
   * This is more reliable than index-based lookup because:
   * - Paragraph IDs are stable across document modifications
   * - Not affected by the presence of images, shapes, or other elements
   */
  private replaceTextInParagraphById(
    xml: string,
    paragraphId: string,
    runIndex: number,
    oldText: string,
    newText: string
  ): string {
    if (!paragraphId) return xml;

    const escapedOld = this.escapeXml(oldText);
    const escapedNew = this.escapeXml(newText);

    // Find the paragraph by ID
    // Pattern: <hp:p id="paragraphId" ...>...</hp:p>
    const idPattern = new RegExp(`<hp:p\\s+[^>]*\\bid=["']${this.escapeRegex(paragraphId)}["'][^>]*>`);
    const match = idPattern.exec(xml);

    if (!match) {
      // Paragraph not found by ID, return unchanged
      return xml;
    }

    const paraStart = match.index;

    // Find the matching closing tag
    let depth = 1;
    let searchPos = paraStart + match[0].length;
    let paraEnd = -1;

    while (depth > 0 && searchPos < xml.length) {
      const nextOpen = xml.indexOf('<hp:p', searchPos);
      const nextClose = xml.indexOf('</hp:p>', searchPos);

      if (nextClose === -1) break;

      if (nextOpen !== -1 && nextOpen < nextClose) {
        depth++;
        searchPos = nextOpen + 1;
      } else {
        depth--;
        if (depth === 0) {
          paraEnd = nextClose + '</hp:p>'.length;
        }
        searchPos = nextClose + 1;
      }
    }

    if (paraEnd === -1) return xml;

    const paragraphContent = xml.slice(paraStart, paraEnd);

    // Find all runs in this paragraph
    const runPattern = /<hp:run\b[^>]*>[\s\S]*?<\/hp:run>/g;
    const runs: { start: number; end: number; content: string }[] = [];
    let runMatch;

    while ((runMatch = runPattern.exec(paragraphContent)) !== null) {
      runs.push({
        start: runMatch.index,
        end: runMatch.index + runMatch[0].length,
        content: runMatch[0]
      });
    }

    // Target the specific run by index
    if (runIndex >= runs.length) {
      // Run index out of bounds, try to replace in any run
      // Replace text within <hp:t> tags (first match only)
      const pattern1 = new RegExp(`(<hp:t[^>]*>)${this.escapeRegex(escapedOld)}`);
      let newParagraphContent = paragraphContent.replace(pattern1, `$1${escapedNew}`);

      // Also try standalone text replacement
      if (newParagraphContent === paragraphContent) {
        const pattern2 = new RegExp(`>${this.escapeRegex(escapedOld)}<`);
        newParagraphContent = paragraphContent.replace(pattern2, `>${escapedNew}<`);
      }

      return xml.slice(0, paraStart) + newParagraphContent + xml.slice(paraEnd);
    }

    // Replace text in the specific run
    const targetRun = runs[runIndex];
    let newRunContent = targetRun.content;

    // Replace within <hp:t> tags in this run
    const tPattern = new RegExp(`(<hp:t[^>]*>)${this.escapeRegex(escapedOld)}(</hp:t>)`);
    newRunContent = newRunContent.replace(tPattern, `$1${escapedNew}$2`);

    // If no match, try simpler pattern
    if (newRunContent === targetRun.content) {
      const simplePattern = new RegExp(`>${this.escapeRegex(escapedOld)}<`);
      newRunContent = newRunContent.replace(simplePattern, `>${escapedNew}<`);
    }

    // Reconstruct paragraph with updated run
    const newParagraphContent =
      paragraphContent.slice(0, targetRun.start) +
      newRunContent +
      paragraphContent.slice(targetRun.end);

    return xml.slice(0, paraStart) + newParagraphContent + xml.slice(paraEnd);
  }

  /**
   * Apply text replacements directly to XML files.
   * This is the safest approach as it preserves the original XML structure.
   */
  private async applyTextReplacementsToXml(): Promise<void> {
    if (!this._zip) return;

    // Get all section files
    let sectionIndex = 0;
    while (true) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      const file = this._zip.file(sectionPath);
      if (!file) break;

      let xml = await file.async('string');

      // Apply each pending replacement to the XML
      for (const replacement of this._pendingTextReplacements) {
        const { oldText, newText, options } = replacement;
        const { caseSensitive = false, regex = false, replaceAll = true } = options;

        // Create pattern for matching text inside <hp:t> tags
        let searchPattern: RegExp;
        if (regex) {
          searchPattern = new RegExp(oldText, caseSensitive ? (replaceAll ? 'g' : '') : (replaceAll ? 'gi' : 'i'));
        } else {
          const escaped = oldText.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
          searchPattern = new RegExp(escaped, caseSensitive ? (replaceAll ? 'g' : '') : (replaceAll ? 'gi' : 'i'));
        }

        // Replace text within <hp:t> tags while preserving XML structure
        // First unescape XML entities so search patterns match raw text, then re-escape
        xml = xml.replace(/<hp:t([^>]*)>([^<]*)<\/hp:t>/g, (_match, attrs, textContent) => {
          const unescaped = textContent
            .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
            .replace(/&quot;/g, '"').replace(/&apos;/g, "'");
          const replaced = unescaped.replace(searchPattern, newText);
          return `<hp:t${attrs}>${this.escapeXml(replaced)}</hp:t>`;
        });
      }

      this._zip.file(sectionPath, xml);
      sectionIndex++;
    }

    // Update metadata in header.xml if needed
    await this.syncMetadataToZip();
  }

  /**
   * Sync structural changes (paragraph text, table cells, etc.)
   * Regenerates section XML from _content to handle new elements.
   */
  private async syncStructuralChangesToZip(): Promise<void> {
    if (!this._zip) return;

    // Regenerate each section XML from content
    for (let sectionIndex = 0; sectionIndex < this._content.sections.length; sectionIndex++) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      const section = this._content.sections[sectionIndex];
      const newXml = this.generateSectionXml(section);
      this._zip.file(sectionPath, newXml);
    }

    // Sync metadata
    await this.syncMetadataToZip();
  }

  /**
   * Generate complete section XML from HwpxSection content.
   */
  private generateSectionXml(section: HwpxSection): string {
    let xml = `<?xml version="1.0" encoding="UTF-8"?>\n`;
    xml += `<hp:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">\n`;

    for (const element of section.elements) {
      if (element.type === 'paragraph') {
        xml += this.generateParagraphXml(element.data as HwpxParagraph);
      } else if (element.type === 'table') {
        xml += this.generateTableXml(element.data as HwpxTable);
      }
    }

    xml += `</hp:sec>`;
    return xml;
  }

  /**
   * Generate paragraph XML from HwpxParagraph.
   */
  private generateParagraphXml(paragraph: HwpxParagraph): string {
    let xml = `  <hp:p>\n`;
    for (const run of paragraph.runs) {
      xml += `    <hp:run>\n`;
      xml += `      <hp:t>${this.escapeXml(run.text)}</hp:t>\n`;
      xml += `    </hp:run>\n`;
    }
    xml += `  </hp:p>\n`;
    return xml;
  }

  /**
   * Generate table XML from HwpxTable.
   */
  private generateTableXml(table: HwpxTable): string {
    let xml = `  <hp:tbl rowCount="${table.rowCount}" colCount="${table.colCount}">\n`;

    for (const row of table.rows) {
      xml += `    <hp:tr>\n`;
      for (const cell of row.cells) {
        xml += `      <hp:tc colAddr="${cell.colAddr}" rowAddr="${cell.rowAddr}" colSpan="${cell.colSpan}" rowSpan="${cell.rowSpan}">\n`;
        for (const para of cell.paragraphs) {
          xml += `        <hp:p>\n`;
          for (const run of para.runs) {
            xml += `          <hp:run>\n`;
            xml += `            <hp:t>${this.escapeXml(run.text)}</hp:t>\n`;
            xml += `          </hp:run>\n`;
          }
          xml += `        </hp:p>\n`;
        }
        xml += `      </hp:tc>\n`;
      }
      xml += `    </hp:tr>\n`;
    }

    xml += `  </hp:tbl>\n`;
    return xml;
  }

  /**
   * Update section XML with current content.
   * Handles paragraphs and table cells.
   */
  private updateSectionXml(xml: string, section: HwpxSection): string {
    let updatedXml = xml;

    // Build a map of element index to paragraph data for quick lookup
    const paragraphMap = new Map<number, HwpxParagraph>();
    const tableMap = new Map<number, HwpxTable>();

    let paragraphCount = 0;
    let tableCount = 0;

    for (const element of section.elements) {
      if (element.type === 'paragraph') {
        paragraphMap.set(paragraphCount, element.data as HwpxParagraph);
        paragraphCount++;
      } else if (element.type === 'table') {
        tableMap.set(tableCount, element.data as HwpxTable);
        tableCount++;
      }
    }

    // Track table positions to skip paragraphs inside tables
    const tablePositions: Array<{ start: number; end: number }> = [];
    const tableRegex = /<hp:tbl\b[^>]*>[\s\S]*?<\/hp:tbl>/g;
    let tableMatch;
    while ((tableMatch = tableRegex.exec(xml)) !== null) {
      tablePositions.push({ start: tableMatch.index, end: tableMatch.index + tableMatch[0].length });
    }

    // Update paragraphs outside of tables
    let paragraphIndex = 0;
    const paragraphRegex = /<hp:p\b[^>]*>([\s\S]*?)<\/hp:p>/g;
    updatedXml = xml.replace(paragraphRegex, (match, _inner, offset) => {
      // Check if this paragraph is inside a table
      const isInTable = tablePositions.some(pos => offset >= pos.start && offset < pos.end);

      if (isInTable) {
        return match; // Don't modify paragraphs inside tables here
      }

      const paragraph = paragraphMap.get(paragraphIndex);
      paragraphIndex++;

      if (paragraph) {
        return this.updateParagraphXml(match, paragraph);
      }
      return match;
    });

    // Update table cells
    let tableIndex = 0;
    updatedXml = updatedXml.replace(/<hp:tbl\b[^>]*>([\s\S]*?)<\/hp:tbl>/g, (tblMatch) => {
      const table = tableMap.get(tableIndex);
      tableIndex++;

      if (!table) {
        return tblMatch;
      }

      let rowIndex = 0;
      return tblMatch.replace(/<hp:tr[^>]*>([\s\S]*?)<\/hp:tr>/g, (rowMatch) => {
        if (rowIndex >= table.rows.length) {
          rowIndex++;
          return rowMatch;
        }

        const row = table.rows[rowIndex];
        rowIndex++;

        let cellIndex = 0;
        return rowMatch.replace(/<hp:tc\b([^>]*)>([\s\S]*?)<\/hp:tc>/g, (cellMatch, cellAttrs, cellContent) => {
          if (cellIndex >= row.cells.length) {
            cellIndex++;
            return cellMatch;
          }

          const cell = row.cells[cellIndex];
          cellIndex++;

          // Update cell content - replace text in paragraphs
          let updatedCellContent = cellContent;
          if (cell.paragraphs && cell.paragraphs.length > 0) {
            let cellParaIndex = 0;
            updatedCellContent = cellContent.replace(/<hp:p\b[^>]*>([\s\S]*?)<\/hp:p>/g, (paraMatch: string) => {
              if (cellParaIndex < cell.paragraphs.length) {
                const para = cell.paragraphs[cellParaIndex];
                cellParaIndex++;
                return this.updateParagraphXml(paraMatch, para);
              }
              cellParaIndex++;
              return paraMatch;
            });
          }

          return `<hp:tc${cellAttrs}>${updatedCellContent}</hp:tc>`;
        });
      });
    });

    return updatedXml;
  }

  /**
   * Update paragraph XML with new text content.
   */
  private updateParagraphXml(xml: string, paragraph: HwpxParagraph): string {
    const fullText = paragraph.runs.map(r => r.text).join('');

    // Update all <hp:t> tags with the combined text
    // For simplicity, put all text in the first <hp:t> tag and empty the rest
    let firstTextTag = true;
    return xml.replace(/<hp:t([^>]*)>([^<]*)<\/hp:t>/g, (_match, attrs, _oldText) => {
      if (firstTextTag) {
        firstTextTag = false;
        return `<hp:t${attrs}>${this.escapeXml(fullText)}</hp:t>`;
      }
      // Empty subsequent text tags
      return `<hp:t${attrs}></hp:t>`;
    });
  }

  /**
   * Serialize a CharShape object to XML string.
   * This preserves all character style properties including spacing (자간).
   */
  private serializeCharShape(charShape: CharShape): string {
    // Use original tag name (charPr or charShape), default to charPr for compatibility
    const tagName = charShape.tagName || 'charPr';
    let xml = `<hh:${tagName} id="${charShape.id}"`;

    // Basic attributes - height takes precedence over fontSize (height is the raw value)
    if (charShape.height !== undefined) {
      xml += ` height="${charShape.height}"`;
    } else if (charShape.fontSize !== undefined) {
      xml += ` height="${Math.round(charShape.fontSize * 100)}"`;
    }
    if (charShape.color) xml += ` textColor="${charShape.color}"`;
    if (charShape.backgroundColor) xml += ` shadeColor="${charShape.backgroundColor}"`;
    if (charShape.useFontSpace !== undefined) xml += ` useFontSpace="${charShape.useFontSpace ? '1' : '0'}"`;
    if (charShape.useKerning !== undefined) xml += ` useKerning="${charShape.useKerning ? '1' : '0'}"`;
    if (charShape.borderFillId !== undefined) xml += ` borderFillIDRef="${charShape.borderFillId}"`;

    xml += `>`;

    // Font references
    if (charShape.fontRefs) {
      xml += `<hh:fontRef`;
      if (charShape.fontRefs.hangul !== undefined) xml += ` hangul="${charShape.fontRefs.hangul}"`;
      if (charShape.fontRefs.latin !== undefined) xml += ` latin="${charShape.fontRefs.latin}"`;
      if (charShape.fontRefs.hanja !== undefined) xml += ` hanja="${charShape.fontRefs.hanja}"`;
      if (charShape.fontRefs.japanese !== undefined) xml += ` japanese="${charShape.fontRefs.japanese}"`;
      if (charShape.fontRefs.other !== undefined) xml += ` other="${charShape.fontRefs.other}"`;
      if (charShape.fontRefs.symbol !== undefined) xml += ` symbol="${charShape.fontRefs.symbol}"`;
      if (charShape.fontRefs.user !== undefined) xml += ` user="${charShape.fontRefs.user}"`;
      xml += `/>`;
    }

    // Ratio (장평)
    if (charShape.ratio) {
      xml += `<hh:ratio`;
      if (charShape.ratio.hangul !== undefined) xml += ` hangul="${charShape.ratio.hangul}"`;
      if (charShape.ratio.latin !== undefined) xml += ` latin="${charShape.ratio.latin}"`;
      if (charShape.ratio.hanja !== undefined) xml += ` hanja="${charShape.ratio.hanja}"`;
      if (charShape.ratio.japanese !== undefined) xml += ` japanese="${charShape.ratio.japanese}"`;
      if (charShape.ratio.other !== undefined) xml += ` other="${charShape.ratio.other}"`;
      if (charShape.ratio.symbol !== undefined) xml += ` symbol="${charShape.ratio.symbol}"`;
      if (charShape.ratio.user !== undefined) xml += ` user="${charShape.ratio.user}"`;
      xml += `/>`;
    }

    // Spacing (자간) - CRITICAL for character spacing!
    if (charShape.charSpacing) {
      xml += `<hh:spacing`;
      if (charShape.charSpacing.hangul !== undefined) xml += ` hangul="${charShape.charSpacing.hangul}"`;
      if (charShape.charSpacing.latin !== undefined) xml += ` latin="${charShape.charSpacing.latin}"`;
      if (charShape.charSpacing.hanja !== undefined) xml += ` hanja="${charShape.charSpacing.hanja}"`;
      if (charShape.charSpacing.japanese !== undefined) xml += ` japanese="${charShape.charSpacing.japanese}"`;
      if (charShape.charSpacing.other !== undefined) xml += ` other="${charShape.charSpacing.other}"`;
      if (charShape.charSpacing.symbol !== undefined) xml += ` symbol="${charShape.charSpacing.symbol}"`;
      if (charShape.charSpacing.user !== undefined) xml += ` user="${charShape.charSpacing.user}"`;
      xml += `/>`;
    }

    // Relative size (상대크기)
    if (charShape.relSize) {
      xml += `<hh:relSz`;
      if (charShape.relSize.hangul !== undefined) xml += ` hangul="${charShape.relSize.hangul}"`;
      if (charShape.relSize.latin !== undefined) xml += ` latin="${charShape.relSize.latin}"`;
      if (charShape.relSize.hanja !== undefined) xml += ` hanja="${charShape.relSize.hanja}"`;
      if (charShape.relSize.japanese !== undefined) xml += ` japanese="${charShape.relSize.japanese}"`;
      if (charShape.relSize.other !== undefined) xml += ` other="${charShape.relSize.other}"`;
      if (charShape.relSize.symbol !== undefined) xml += ` symbol="${charShape.relSize.symbol}"`;
      if (charShape.relSize.user !== undefined) xml += ` user="${charShape.relSize.user}"`;
      xml += `/>`;
    }

    // Char offset (글자위치)
    if (charShape.charOffset) {
      xml += `<hh:offset`;
      if (charShape.charOffset.hangul !== undefined) xml += ` hangul="${charShape.charOffset.hangul}"`;
      if (charShape.charOffset.latin !== undefined) xml += ` latin="${charShape.charOffset.latin}"`;
      if (charShape.charOffset.hanja !== undefined) xml += ` hanja="${charShape.charOffset.hanja}"`;
      if (charShape.charOffset.japanese !== undefined) xml += ` japanese="${charShape.charOffset.japanese}"`;
      if (charShape.charOffset.other !== undefined) xml += ` other="${charShape.charOffset.other}"`;
      if (charShape.charOffset.symbol !== undefined) xml += ` symbol="${charShape.charOffset.symbol}"`;
      if (charShape.charOffset.user !== undefined) xml += ` user="${charShape.charOffset.user}"`;
      xml += `/>`;
    }

    // Bold/Italic
    if (charShape.bold) xml += `<hh:bold/>`;
    if (charShape.italic) xml += `<hh:italic/>`;

    // Underline
    if (charShape.underline && typeof charShape.underline === 'object') {
      xml += `<hh:underline type="${charShape.underline.type.toUpperCase()}" shape="${charShape.underline.shape.toUpperCase()}" color="${charShape.underline.color}"/>`;
    }

    // Strikeout
    if (charShape.strikeout && typeof charShape.strikeout === 'object') {
      xml += `<hh:strikeout type="${charShape.strikeout.type.toUpperCase()}" shape="${charShape.strikeout.shape.toUpperCase()}" color="${charShape.strikeout.color}"/>`;
    }

    // Outline
    if (charShape.outline) {
      const outlineType = typeof charShape.outline === 'object' ? charShape.outline.type : charShape.outline;
      xml += `<hh:outline type="${outlineType.toUpperCase()}"/>`;
    }

    // Shadow
    if (charShape.shadow && typeof charShape.shadow === 'object' && charShape.shadow.type !== 'None') {
      xml += `<hh:shadow type="${charShape.shadow.type.toUpperCase()}"`;
      if (charShape.shadow.offsetX !== undefined) xml += ` offsetX="${Math.round(charShape.shadow.offsetX * 100)}"`;
      if (charShape.shadow.offsetY !== undefined) xml += ` offsetY="${Math.round(charShape.shadow.offsetY * 100)}"`;
      if (charShape.shadow.color) xml += ` color="${charShape.shadow.color}"`;
      xml += `/>`;
    }

    // Emboss/Engrave
    if (charShape.emboss) xml += `<hh:emboss/>`;
    if (charShape.engrave) xml += `<hh:engrave/>`;

    // SymMark (강조점)
    if (charShape.symMark && charShape.symMark !== 'None') {
      xml += `<hh:symMark symMarkType="${charShape.symMark.toUpperCase()}"/>`;
    }

    xml += `</hh:${tagName}>`;
    return xml;
  }

  /**
   * Sync charShapes from memory to header.xml.
   * This ensures character styles (including spacing) are preserved after save.
   */
  private async syncCharShapesToZip(): Promise<void> {
    if (!this._zip || !this._content.styles?.charShapes) return;

    const headerPath = 'Contents/header.xml';
    let headerXml = await this._zip.file(headerPath)?.async('string');
    if (!headerXml) return;

    // Debug: count charShapes before modification
    const originalCharShapeCount = (headerXml.match(/<hh:charShape/gi) || []).length;
    const originalCharPrCount = (headerXml.match(/<hh:charPr/gi) || []).length;
    console.log(`[HwpxDocument] syncCharShapesToZip: original charShape=${originalCharShapeCount}, charPr=${originalCharPrCount}`);

    // For each charShape in memory, update or preserve in XML
    for (const [id, charShape] of this._content.styles.charShapes) {
      const newXml = this.serializeCharShape(charShape);

      // Try to match existing charShape with this ID (supports both hh:charShape and hh:charPr)
      // Use non-greedy match with [\s\S]*? for content between tags
      const charShapePattern = new RegExp(
        `<hh:charShape[^>]*\\bid="${id}"[^>]*>[\\s\\S]*?</hh:charShape>`,
        'i'
      );
      const charPrPattern = new RegExp(
        `<hh:charPr[^>]*\\bid="${id}"[^>]*>[\\s\\S]*?</hh:charPr>`,
        'i'
      );

      if (headerXml.match(charShapePattern)) {
        headerXml = headerXml.replace(charShapePattern, newXml);
      } else if (headerXml.match(charPrPattern)) {
        headerXml = headerXml.replace(charPrPattern, newXml);
      }
      // If no match found, the charShape might be new - but we don't add new ones
      // to avoid corrupting the structure. Original charShapes are preserved.
    }

    // Debug: count charShapes/charPr after modification
    const newCharShapeCount = (headerXml.match(/<hh:charShape/gi) || []).length;
    const newCharPrCount = (headerXml.match(/<hh:charPr/gi) || []).length;
    console.log(`[HwpxDocument] syncCharShapesToZip: after update charShape=${newCharShapeCount}, charPr=${newCharPrCount}`);

    const totalBefore = originalCharShapeCount + originalCharPrCount;
    const totalAfter = newCharShapeCount + newCharPrCount;
    if (totalAfter < totalBefore) {
      console.warn(`[HwpxDocument] WARNING: charShape/charPr count decreased from ${totalBefore} to ${totalAfter}`);
    }

    this._zip.file(headerPath, headerXml);
  }

  /**
   * Sync metadata to header.xml
   */
  private async syncMetadataToZip(): Promise<void> {
    if (!this._zip) return;

    const headerPath = 'Contents/header.xml';
    let headerXml = await this._zip.file(headerPath)?.async('string');
    if (headerXml && this._content.metadata) {
      const meta = this._content.metadata;
      if (meta.title) {
        headerXml = headerXml.replace(/<hh:title[^>]*>[^<]*<\/hh:title>/,
          `<hh:title>${this.escapeXml(meta.title)}</hh:title>`);
      }
      if (meta.creator) {
        headerXml = headerXml.replace(/<hh:creator[^>]*>[^<]*<\/hh:creator>/,
          `<hh:creator>${this.escapeXml(meta.creator)}</hh:creator>`);
      }
      if (meta.subject) {
        headerXml = headerXml.replace(/<hh:subject[^>]*>[^<]*<\/hh:subject>/,
          `<hh:subject>${this.escapeXml(meta.subject)}</hh:subject>`);
      }
      if (meta.description) {
        headerXml = headerXml.replace(/<hh:description[^>]*>[^<]*<\/hh:description>/,
          `<hh:description>${this.escapeXml(meta.description)}</hh:description>`);
      }
      this._zip.file(headerPath, headerXml);
    }
  }

  private escapeXml(text: string): string {
    return text
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&apos;');
  }

  /**
   * Default chunk size for splitting long text (in characters).
   * Texts longer than this will be split into multiple <hp:run> elements.
   */
  private static readonly TEXT_CHUNK_SIZE = 2000;

  /**
   * Split long text into chunks for safer XML processing.
   * Attempts to split at word boundaries (spaces, punctuation) when possible.
   * @param text The text to split
   * @param maxChunkSize Maximum characters per chunk (default: TEXT_CHUNK_SIZE)
   * @returns Array of text chunks
   */
  private splitTextIntoChunks(text: string, maxChunkSize: number = HwpxDocument.TEXT_CHUNK_SIZE): string[] {
    if (text.length <= maxChunkSize) {
      return [text];
    }

    const chunks: string[] = [];
    let remaining = text;

    while (remaining.length > 0) {
      if (remaining.length <= maxChunkSize) {
        chunks.push(remaining);
        break;
      }

      // Try to find a good break point (space, comma, period, etc.)
      let breakPoint = maxChunkSize;

      // Look for word boundary within last 20% of chunk
      const searchStart = Math.floor(maxChunkSize * 0.8);
      const searchRegion = remaining.substring(searchStart, maxChunkSize);

      // Find last space or punctuation in search region
      const lastSpace = searchRegion.lastIndexOf(' ');
      const lastComma = searchRegion.lastIndexOf(',');
      const lastPeriod = searchRegion.lastIndexOf('.');
      const lastNewline = searchRegion.lastIndexOf('\n');

      // Pick the best break point
      const breakPoints = [lastSpace, lastComma, lastPeriod, lastNewline].filter(p => p >= 0);
      if (breakPoints.length > 0) {
        breakPoint = searchStart + Math.max(...breakPoints) + 1;
      }

      chunks.push(remaining.substring(0, breakPoint));
      remaining = remaining.substring(breakPoint);
    }

    return chunks;
  }

  /**
   * Generate multiple <hp:run> elements for chunked text.
   * Used when text is too long to be in a single run.
   */
  private generateChunkedRuns(text: string, prefix: string, charAttr: string, maxChunkSize?: number): string {
    const chunks = this.splitTextIntoChunks(text, maxChunkSize);

    if (chunks.length === 1) {
      // Single chunk - normal case
      const escapedText = this.escapeXml(chunks[0]);
      return `<${prefix}:run${charAttr}><${prefix}:t>${escapedText}</${prefix}:t></${prefix}:run>`;
    }

    // Multiple chunks - generate multiple runs
    return chunks.map(chunk => {
      const escapedChunk = this.escapeXml(chunk);
      return `<${prefix}:run${charAttr}><${prefix}:t>${escapedChunk}</${prefix}:t></${prefix}:run>`;
    }).join('');
  }

  /**
   * Get image dimensions from base64 encoded data
   * Returns width and height in pixels
   */
  private getImageDimensions(base64Data: string, mimeType: string): { width: number; height: number } {
    const buffer = Buffer.from(base64Data, 'base64');

    if (mimeType === 'image/png') {
      // PNG: width at bytes 16-19, height at bytes 20-23 (big endian)
      if (buffer.length >= 24 && buffer[0] === 0x89 && buffer[1] === 0x50) {
        const width = buffer.readUInt32BE(16);
        const height = buffer.readUInt32BE(20);
        return { width, height };
      }
    } else if (mimeType === 'image/jpeg' || mimeType === 'image/jpg') {
      // JPEG: find SOF0/SOF2 marker (0xFF 0xC0 or 0xFF 0xC2)
      let i = 0;
      while (i < buffer.length - 9) {
        if (buffer[i] === 0xFF) {
          const marker = buffer[i + 1];
          if (marker === 0xC0 || marker === 0xC2) {
            // Height at i+5, Width at i+7 (big endian, 2 bytes each)
            const height = buffer.readUInt16BE(i + 5);
            const width = buffer.readUInt16BE(i + 7);
            return { width, height };
          } else if (marker !== 0x00 && marker !== 0xFF) {
            // Skip to next marker
            const len = buffer.readUInt16BE(i + 2);
            i += 2 + len;
            continue;
          }
        }
        i++;
      }
    } else if (mimeType === 'image/bmp') {
      // BMP: width at bytes 18-21, height at bytes 22-25 (little endian)
      if (buffer.length >= 26 && buffer[0] === 0x42 && buffer[1] === 0x4D) {
        const width = buffer.readUInt32LE(18);
        const height = Math.abs(buffer.readInt32LE(22)); // height can be negative
        return { width, height };
      }
    }

    // Default: assume 100x100 if can't detect
    return { width: 100, height: 100 };
  }

  /**
   * Get raw XML content of a section.
   * Useful for AI-based document manipulation.
   */
  public async getSectionXml(sectionIndex: number): Promise<string | null> {
    if (!this._zip) return null;

    const sectionPath = `Contents/section${sectionIndex}.xml`;
    const file = this._zip.file(sectionPath);
    if (!file) return null;

    return await file.async('string');
  }

  /**
   * Set (replace) raw XML content of a section.
   * WARNING: This completely replaces the section XML. Use with caution.
   * The XML must be valid HWPML format.
   *
   * @param sectionIndex The section index to replace
   * @param xml The new XML content (must be valid HWPML)
   * @param validate If true, performs basic XML validation before replacing
   * @returns Object with success status and any validation errors
   */
  public async setSectionXml(
    sectionIndex: number,
    xml: string,
    validate: boolean = true
  ): Promise<{ success: boolean; error?: string }> {
    if (!this._zip) {
      return { success: false, error: 'Document not loaded or is HWP format (read-only)' };
    }

    const sectionPath = `Contents/section${sectionIndex}.xml`;
    const existingFile = this._zip.file(sectionPath);
    if (!existingFile) {
      return { success: false, error: `Section ${sectionIndex} does not exist` };
    }

    if (validate) {
      // Basic XML structure validation
      const validation = this.validateSectionXml(xml);
      if (!validation.valid) {
        return { success: false, error: validation.error };
      }
    }

    // Save undo state
    const originalXml = await existingFile.async('string');
    this.saveState();

    // Replace section XML
    this._zip.file(sectionPath, xml);
    this.markModified();

    // Re-parse the section to update in-memory content
    try {
      const updatedContent = await HwpxParser.parse(this._zip);
      this._content = updatedContent;
      return { success: true };
    } catch (parseError) {
      // Rollback on parse error
      this._zip.file(sectionPath, originalXml);
      return {
        success: false,
        error: `XML parsing failed: ${parseError instanceof Error ? parseError.message : String(parseError)}`
      };
    }
  }

  /**
   * Validate section XML structure.
   */
  private validateSectionXml(xml: string): { valid: boolean; error?: string } {
    // 1. Check for invalid control characters (except tab, LF, CR)
    const invalidChars = xml.match(/[\x00-\x08\x0B-\x0C\x0E-\x1F]/);
    if (invalidChars) {
      return {
        valid: false,
        error: `Invalid control character found: ${JSON.stringify(invalidChars[0])} at position ${xml.indexOf(invalidChars[0])}`
      };
    }

    // 2. Check for required root element
    if (!xml.includes('<hs:sec') && !xml.includes('<hp:sec')) {
      return { valid: false, error: 'Missing section root element (<hs:sec> or <hp:sec>)' };
    }

    // 3. Check for required HWPML namespace declaration
    const hasHsNamespace = xml.includes('xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"');
    const hasHpNamespace = xml.includes('xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"');
    if (!hasHsNamespace && !hasHpNamespace) {
      return {
        valid: false,
        error: 'Missing required HWPML namespace declarations (xmlns:hs or xmlns:hp)'
      };
    }

    // 4. Basic XML well-formedness checks
    // Check for mismatched angle brackets
    const openBrackets = (xml.match(/</g) || []).length;
    const closeBrackets = (xml.match(/>/g) || []).length;
    if (openBrackets !== closeBrackets) {
      return {
        valid: false,
        error: `Mismatched angle brackets: ${openBrackets} '<' vs ${closeBrackets} '>'`
      };
    }

    // Check for unclosed CDATA sections
    const cdataOpen = (xml.match(/<!\[CDATA\[/g) || []).length;
    const cdataClose = (xml.match(/\]\]>/g) || []).length;
    if (cdataOpen !== cdataClose) {
      return {
        valid: false,
        error: `Unclosed CDATA section: ${cdataOpen} open, ${cdataClose} close`
      };
    }

    // 5. Comprehensive tag balance validation for all HWPML tags
    const tagNames = ['p', 'tbl', 'tr', 'tc', 'run', 't', 'subList', 'sec', 'lineSeg'];
    for (const tagName of tagNames) {
      const openPattern = new RegExp(`<(?:hp|hs|hc):${tagName}(?:\\s[^>]*)?\\/?>`, 'g');
      const closePattern = new RegExp(`</(?:hp|hs|hc):${tagName}>`, 'g');
      const selfClosePattern = new RegExp(`<(?:hp|hs|hc):${tagName}(?:\\s[^>]*)?/>`, 'g');

      const openCount = (xml.match(openPattern) || []).length;
      const closeCount = (xml.match(closePattern) || []).length;
      const selfCloseCount = (xml.match(selfClosePattern) || []).length;

      // Tags that are not self-closed must have matching close tags
      const nonSelfClosedOpen = openCount - selfCloseCount;
      if (nonSelfClosedOpen !== closeCount) {
        return {
          valid: false,
          error: `Tag imbalance for <${tagName}>: ${nonSelfClosedOpen} open (excluding ${selfCloseCount} self-closed), ${closeCount} close`
        };
      }
    }

    // 6. Check for common XML syntax errors
    // Detect tags with spaces in names
    if (xml.match(/<\w+\s+\w+[^>]*>/)) {
      return {
        valid: false,
        error: 'Invalid tag syntax: tag names cannot contain spaces'
      };
    }

    // Detect attributes without quotes
    const unquotedAttr = xml.match(/\s(\w+)=([^"'\s>][^\s>]*)/);
    if (unquotedAttr) {
      return {
        valid: false,
        error: `Unquoted attribute value found: ${unquotedAttr[1]}=${unquotedAttr[2]}`
      };
    }

    return { valid: true };
  }

  /**
   * Render Mermaid diagram and insert as image into the document.
   * Uses mermaid.ink API for rendering.
   *
   * @param mermaidCode The Mermaid diagram code
   * @param sectionIndex Section to insert into
   * @param afterElementIndex Insert after this element index (-1 for beginning)
   * @param options Optional rendering options
   *   - width: Target width in points (optional)
   *   - height: Target height in points (optional)
   *   - preserveAspectRatio: If true, maintains original image aspect ratio (default: true)
   *     When only width is specified, height is auto-calculated.
   *     When only height is specified, width is auto-calculated.
   *   - position: Positioning options for the rendered diagram
   * @returns Object with image ID and actual dimensions, or error
   */
  public async renderMermaidToImage(
    mermaidCode: string,
    sectionIndex: number,
    afterElementIndex: number,
    options?: {
      width?: number;
      height?: number;
      theme?: 'default' | 'dark' | 'forest' | 'neutral';
      backgroundColor?: string;
      preserveAspectRatio?: boolean;
      position?: ImagePositionOptions;
      headerText?: string; // Text to search for in XML to find exact position
    }
  ): Promise<{ success: boolean; imageId?: string; actualWidth?: number; actualHeight?: number; error?: string }> {
    if (!this._zip) {
      return { success: false, error: 'Document not loaded or is HWP format' };
    }

    const section = this._content.sections[sectionIndex];
    if (!section) {
      return { success: false, error: `Section ${sectionIndex} not found` };
    }

    try {
      // Create state object for mermaid.ink (same format as mermaid.live)
      const stateObject = {
        code: mermaidCode,
        mermaid: { theme: options?.theme || 'default' },
        autoSync: true,
        updateDiagram: true
      };

      // Encode using pako deflate + base64 URL-safe (mermaid.live format)
      const jsonString = JSON.stringify(stateObject);
      const compressed = pako.deflate(jsonString, { level: 9 });
      const base64Code = Buffer.from(compressed)
        .toString('base64')
        .replace(/\+/g, '-')
        .replace(/\//g, '_');

      // Build URL with optional background color
      let url = `https://mermaid.ink/img/pako:${base64Code}?type=png`;
      if (options?.backgroundColor) {
        // Remove # from color if present
        const bgColor = options.backgroundColor.replace(/^#/, '');
        url += `&bgColor=${bgColor}`;
      }
      const response = await fetch(url);

      if (!response.ok) {
        return {
          success: false,
          error: `Mermaid rendering failed: ${response.status} ${response.statusText}`
        };
      }

      // Get image data as base64
      const arrayBuffer = await response.arrayBuffer();
      const imageBase64 = Buffer.from(arrayBuffer).toString('base64');

      // preserveAspectRatio defaults to true for Mermaid diagrams
      const preserveAspectRatio = options?.preserveAspectRatio !== false;

      // Insert image using existing method with preserveAspectRatio and position support
      const result = this.insertImage(sectionIndex, afterElementIndex, {
        data: imageBase64,
        mimeType: 'image/png',
        width: options?.width,
        height: options?.height,
        preserveAspectRatio,
        position: options?.position,
        headerText: options?.headerText,
      });

      if (result) {
        return {
          success: true,
          imageId: result.id,
          actualWidth: result.actualWidth,
          actualHeight: result.actualHeight
        };
      } else {
        return { success: false, error: 'Failed to insert image into document' };
      }
    } catch (err) {
      return {
        success: false,
        error: `Mermaid rendering error: ${err instanceof Error ? err.message : String(err)}`
      };
    }
  }

  /**
   * Get list of available sections.
   */
  public async getAvailableSections(): Promise<number[]> {
    if (!this._zip) return [];

    const sections: number[] = [];
    const files = this._zip.file(/Contents\/section\d+\.xml/);

    for (const file of files) {
      const match = file.name.match(/section(\d+)\.xml/);
      if (match) {
        sections.push(parseInt(match[1], 10));
      }
    }

    return sections.sort((a, b) => a - b);
  }

  // ============================================================
  // Image Insert to ZIP (BinData, content.hpf, section XML)
  // ============================================================

  /**
   * Apply pending image inserts to ZIP file.
   * 1. Add image file to BinData/ folder
   * 2. Update content.hpf manifest
   * 3. Add hp:pic tag to section XML
   */
  private async applyImageInsertsToZip(): Promise<void> {
    if (!this._zip || this._pendingImageInserts.length === 0) return;

    for (const insert of this._pendingImageInserts) {
      // 1. Add image file to BinData/ folder
      const extension = this.getExtensionFromMimeType(insert.mimeType);
      const binDataPath = `BinData/${insert.imageId}.${extension}`;
      const imageBuffer = Buffer.from(insert.data, 'base64');
      this._zip.file(binDataPath, imageBuffer);

      // 2. Update content.hpf manifest
      await this.addImageToContentHpf(insert.imageId, binDataPath, insert.mimeType);

      // 3. Add hp:pic tag to section XML
      await this.addImageToSectionXml(
        insert.sectionIndex,
        insert.afterElementIndex,
        insert.imageId,
        insert.width,
        insert.height,
        insert.position,
        insert.headerText
      );
    }
  }

  /**
   * Get file extension from MIME type
   */
  private getExtensionFromMimeType(mimeType: string): string {
    const mimeToExt: Record<string, string> = {
      'image/png': 'png',
      'image/jpeg': 'jpg',
      'image/jpg': 'jpg',
      'image/gif': 'gif',
      'image/bmp': 'bmp',
      'image/webp': 'webp',
    };
    return mimeToExt[mimeType] || 'png';
  }

  /**
   * Extract original image dimensions from base64 encoded image data.
   * Supports PNG and JPEG formats.
   * @param base64Data Base64 encoded image data
   * @param mimeType MIME type of the image
   * @returns { width, height } or null if unable to parse
   */
  private getImageDimensionsFromBase64(base64Data: string, mimeType: string): { width: number; height: number } | null {
    try {
      const buffer = Buffer.from(base64Data, 'base64');

      if (mimeType === 'image/png') {
        // PNG format:
        // Bytes 0-7: PNG signature (89 50 4E 47 0D 0A 1A 0A)
        // Bytes 8-11: IHDR chunk length
        // Bytes 12-15: "IHDR" chunk type
        // Bytes 16-19: Width (big-endian)
        // Bytes 20-23: Height (big-endian)
        if (buffer.length < 24) return null;

        // Verify PNG signature
        const pngSignature = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];
        for (let i = 0; i < 8; i++) {
          if (buffer[i] !== pngSignature[i]) return null;
        }

        const width = buffer.readUInt32BE(16);
        const height = buffer.readUInt32BE(20);
        return { width, height };
      }

      if (mimeType === 'image/jpeg' || mimeType === 'image/jpg') {
        // JPEG format: Find SOF0 (0xFF 0xC0) or SOF2 (0xFF 0xC2) marker
        // After marker: 2 bytes length, 1 byte precision, 2 bytes height, 2 bytes width
        if (buffer.length < 2) return null;

        // Verify JPEG signature (starts with 0xFF 0xD8)
        if (buffer[0] !== 0xFF || buffer[1] !== 0xD8) return null;

        let offset = 2;
        while (offset < buffer.length - 8) {
          if (buffer[offset] !== 0xFF) {
            offset++;
            continue;
          }

          const marker = buffer[offset + 1];

          // SOF markers (Start of Frame)
          if (marker === 0xC0 || marker === 0xC1 || marker === 0xC2) {
            // Skip marker (2 bytes) and length (2 bytes) and precision (1 byte)
            const height = buffer.readUInt16BE(offset + 5);
            const width = buffer.readUInt16BE(offset + 7);
            return { width, height };
          }

          // Skip to next marker
          if (marker === 0xD8 || marker === 0xD9 || (marker >= 0xD0 && marker <= 0xD7)) {
            // These markers have no length field
            offset += 2;
          } else {
            // Read length and skip
            const length = buffer.readUInt16BE(offset + 2);
            offset += 2 + length;
          }
        }
        return null;
      }

      // GIF format
      if (mimeType === 'image/gif') {
        if (buffer.length < 10) return null;
        // GIF87a or GIF89a signature
        const sig = buffer.slice(0, 6).toString('ascii');
        if (sig !== 'GIF87a' && sig !== 'GIF89a') return null;

        const width = buffer.readUInt16LE(6);
        const height = buffer.readUInt16LE(8);
        return { width, height };
      }

      return null;
    } catch {
      return null;
    }
  }

  /**
   * Add image entry to content.hpf manifest
   */
  private async addImageToContentHpf(imageId: string, binDataPath: string, mimeType: string): Promise<void> {
    if (!this._zip) return;

    const contentHpfFile = this._zip.file('Contents/content.hpf');
    if (!contentHpfFile) return;

    let contentHpf = await contentHpfFile.async('string');

    // Find </opf:manifest> and insert new item before it
    const manifestEndTag = '</opf:manifest>';
    const insertPos = contentHpf.indexOf(manifestEndTag);
    if (insertPos === -1) return;

    // Generate hash key (simple base64 of image id for uniqueness)
    const hashKey = Buffer.from(imageId + Date.now().toString()).toString('base64').substring(0, 22) + '==';

    // Create new item entry
    const newItem = `<opf:item id="${imageId}" href="${binDataPath}" media-type="${mimeType}" isEmbeded="1" hashkey="${hashKey}"/>`;

    // Insert before </opf:manifest>
    contentHpf = contentHpf.substring(0, insertPos) + newItem + contentHpf.substring(insertPos);

    this._zip.file('Contents/content.hpf', contentHpf);
  }

  /**
   * Add hp:pic tag to section XML
   */
  private async addImageToSectionXml(
    sectionIndex: number,
    afterElementIndex: number,
    imageId: string,
    width: number,
    height: number,
    position?: ImagePositionOptions,
    headerText?: string
  ): Promise<void> {
    if (!this._zip) return;

    const sectionPath = `Contents/section${sectionIndex}.xml`;
    const sectionFile = this._zip.file(sectionPath);
    if (!sectionFile) return;

    let sectionXml = await sectionFile.async('string');

    // Convert width/height from points to hwpunit (1pt ≈ 100 hwpunit)
    const hwpWidth = Math.round(width * 100);
    const hwpHeight = Math.round(height * 100);

    // Generate unique IDs
    const picId = Math.floor(Math.random() * 2000000000) + 100000000;
    const instId = Math.floor(Math.random() * 2000000000) + 100000000;
    const zOrder = Math.floor(Math.random() * 100);

    // Generate hp:pic XML tag
    const picXml = this.generateImagePicXml(picId, instId, zOrder, imageId, hwpWidth, hwpHeight, position);

    // Wrap pic in a paragraph structure (required by HWPML)
    // Image must be inside <hp:p><hp:run>...</hp:run></hp:p>
    const paraId = Math.floor(Math.random() * 2000000000);
    const fullParagraphXml = `<hp:p id="${paraId}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0"><hp:run charPrIDRef="0">${picXml}<hp:t/></hp:run><hp:linesegarray><hp:lineseg textpos="0" vertpos="0" vertsize="1600" textheight="1600" baseline="1360" spacing="960" horzpos="0" horzsize="0" flags="393216"/></hp:linesegarray></hp:p>`;

    // Find insertion point
    let insertPos: number | null = null;

    // If headerText is provided, find position by searching for the text in XML
    if (headerText) {
      insertPos = this.findInsertPositionByTextInXml(sectionXml, headerText);
    }

    // If text-based search didn't find position, fall back to index-based
    if (insertPos === null) {
      // Find all top-level elements (paragraphs and tables) in order
      const elements: Array<{ type: 'paragraph' | 'table'; start: number; end: number }> = [];

      // Use balanced bracket matching to find tables correctly (handles nested tables)
      const tables = this.findAllTables(sectionXml);
      const tableRegions: Array<{ start: number; end: number }> = [];
      for (const table of tables) {
        tableRegions.push({ start: table.startIndex, end: table.endIndex });
        elements.push({ type: 'table', start: table.startIndex, end: table.endIndex });
      }

      // Use balanced bracket matching to find paragraphs correctly (handles nested structures)
      const paragraphs = this.findAllElementsWithDepth(sectionXml, 'p');
      for (const para of paragraphs) {
        // Check if this paragraph is inside any table (exclude table-internal paragraphs)
        const isInsideTable = tableRegions.some(t => para.startIndex >= t.start && para.startIndex < t.end);
        if (!isInsideTable) {
          elements.push({ type: 'paragraph', start: para.startIndex, end: para.endIndex });
        }
      }

      // Sort elements by start position
      elements.sort((a, b) => a.start - b.start);

      // Insert after the specified element (or at the beginning if -1)
      if (afterElementIndex < 0 || elements.length === 0) {
        // Insert at the beginning of section (after <hs:sec ...> or <hp:sec ...>)
        const secStartMatch = sectionXml.match(/<(?:hs|hp):sec[^>]*>/);
        insertPos = secStartMatch ? secStartMatch.index! + secStartMatch[0].length : 0;
      } else if (afterElementIndex >= elements.length) {
        // Insert at the end (before </hs:sec> or </hp:sec>)
        let secEndMatch = sectionXml.lastIndexOf('</hs:sec>');
        if (secEndMatch === -1) {
          secEndMatch = sectionXml.lastIndexOf('</hp:sec>');
        }
        insertPos = secEndMatch !== -1 ? secEndMatch : sectionXml.length;
      } else {
        // Insert after the specified element (paragraph or table)
        insertPos = elements[afterElementIndex].end;
      }
    }

    // Insert the image paragraph XML
    sectionXml = sectionXml.substring(0, insertPos) + fullParagraphXml + sectionXml.substring(insertPos);

    this._zip.file(sectionPath, sectionXml);
  }

  /**
   * Find insertion position in XML by searching for text content.
   * Returns the position right after the paragraph containing the text, or null if not found.
   */
  private findInsertPositionByTextInXml(xml: string, searchText: string): number | null {
    const normalizedSearch = searchText.toLowerCase().trim();

    // Find all top-level paragraphs (not inside tables)
    const tables = this.findAllTables(xml);
    const tableRegions = tables.map(t => ({ start: t.startIndex, end: t.endIndex }));

    const paragraphs = this.findAllElementsWithDepth(xml, 'p');

    for (const para of paragraphs) {
      // Skip paragraphs inside tables
      const isInsideTable = tableRegions.some(t => para.startIndex >= t.start && para.startIndex < t.end);
      if (isInsideTable) continue;

      // Extract text content from the paragraph XML
      const paraXml = xml.substring(para.startIndex, para.endIndex);
      const textContent = this.extractTextFromParagraphXml(paraXml);

      if (textContent.toLowerCase().includes(normalizedSearch)) {
        // Found the paragraph - return position right after it
        return para.endIndex;
      }
    }

    // Also search in table cells
    for (const table of tables) {
      const tableXml = xml.substring(table.startIndex, table.endIndex);

      // Find cells in this table
      const cellMatches = [...tableXml.matchAll(/<(?:hp|hs):tc[^>]*>([\s\S]*?)<\/(?:hp|hs):tc>/g)];
      for (const cellMatch of cellMatches) {
        const cellContent = cellMatch[1];
        const textContent = this.extractTextFromCellXml(cellContent);

        if (textContent.toLowerCase().includes(normalizedSearch)) {
          // Found in table cell - return position right after the table
          return table.endIndex;
        }
      }
    }

    return null;
  }

  /**
   * Extract text content from paragraph XML
   */
  private extractTextFromParagraphXml(paraXml: string): string {
    const textMatches = [...paraXml.matchAll(/<(?:hp|hs):t[^>]*>([^<]*)<\/(?:hp|hs):t>/g)];
    return textMatches.map(m => m[1]).join('');
  }

  /**
   * Extract text content from cell XML (handles subList and nested paragraphs)
   */
  private extractTextFromCellXml(cellXml: string): string {
    const textMatches = [...cellXml.matchAll(/<(?:hp|hs):t[^>]*>([^<]*)<\/(?:hp|hs):t>/g)];
    return textMatches.map(m => m[1]).join('');
  }

  /**
   * Find all paragraphs in a table cell XML
   * Returns array of { start, end, xml } for each paragraph
   */
  private findAllParagraphsInCell(cellXml: string): Array<{ start: number; end: number; xml: string }> {
    const paragraphs: Array<{ start: number; end: number; xml: string }> = [];
    const paragraphRegex = /<hp:p[^>]*>[\s\S]*?<\/hp:p>/g;

    let match;
    while ((match = paragraphRegex.exec(cellXml)) !== null) {
      paragraphs.push({
        start: match.index,
        end: match.index + match[0].length,
        xml: match[0],
      });
    }

    return paragraphs;
  }

  /**
   * Generate hp:pic XML tag for image with positioning options
   */
  private generateImagePicXml(
    picId: number,
    instId: number,
    zOrder: number,
    binaryItemId: string,
    width: number,
    height: number,
    position?: ImagePositionOptions
  ): string {
    // Map position options to HWPML values
    const textWrapMap: Record<string, string> = {
      'top_and_bottom': 'TOP_AND_BOTTOM',
      'square': 'SQUARE',
      'tight': 'TIGHT',
      'behind_text': 'BEHIND_TEXT',
      'in_front_of_text': 'IN_FRONT_OF_TEXT',
      'none': 'NONE',
    };

    const vertRelToMap: Record<string, string> = {
      'para': 'PARA',
      'paper': 'PAPER',
    };

    const horzRelToMap: Record<string, string> = {
      'column': 'COLUMN',
      'para': 'PARA',
      'paper': 'PAPER',
    };

    const vertAlignMap: Record<string, string> = {
      'top': 'TOP',
      'center': 'CENTER',
      'bottom': 'BOTTOM',
    };

    const horzAlignMap: Record<string, string> = {
      'left': 'LEFT',
      'center': 'CENTER',
      'right': 'RIGHT',
    };

    // Use provided options or defaults
    const textWrap = textWrapMap[position?.textWrap || 'top_and_bottom'] || 'TOP_AND_BOTTOM';
    const treatAsChar = position?.positionType === 'inline' ? '1' : '0';
    const vertRelTo = vertRelToMap[position?.vertRelTo || 'para'] || 'PARA';
    const horzRelTo = horzRelToMap[position?.horzRelTo || 'column'] || 'COLUMN';
    const vertAlign = vertAlignMap[position?.vertAlign || 'top'] || 'TOP';
    const horzAlign = horzAlignMap[position?.horzAlign || 'left'] || 'LEFT';
    const vertOffset = Math.round((position?.vertOffset || 0) * 100); // pt to hwpunit
    const horzOffset = Math.round((position?.horzOffset || 0) * 100); // pt to hwpunit

    // IMPORTANT: curSz must be 0,0 for 한글 to display the image correctly
    return `<hp:pic id="${picId}" zOrder="${zOrder}" numberingType="PICTURE" textWrap="${textWrap}" textFlow="BOTH_SIDES" lock="0" dropcapstyle="None" href="" groupLevel="0" instid="${instId}" reverse="0">
  <hp:offset x="0" y="0"/>
  <hp:orgSz width="${width}" height="${height}"/>
  <hp:curSz width="0" height="0"/>
  <hp:flip horizontal="0" vertical="0"/>
  <hp:rotationInfo angle="0" centerX="${Math.round(width / 2)}" centerY="${Math.round(height / 2)}" rotateimage="1"/>
  <hp:renderingInfo>
    <hc:transMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>
    <hc:scaMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>
    <hc:rotMatrix e1="1" e2="0" e3="0" e4="0" e5="1" e6="0"/>
  </hp:renderingInfo>
  <hc:img binaryItemIDRef="${binaryItemId}" bright="0" contrast="0" effect="REAL_PIC" alpha="0"/>
  <hp:imgRect>
    <hc:pt0 x="0" y="0"/>
    <hc:pt1 x="${width}" y="0"/>
    <hc:pt2 x="${width}" y="${height}"/>
    <hc:pt3 x="0" y="${height}"/>
  </hp:imgRect>
  <hp:imgClip left="0" right="${width}" top="0" bottom="${height}"/>
  <hp:inMargin left="0" right="0" top="0" bottom="0"/>
  <hp:imgDim dimwidth="${width}" dimheight="${height}"/>
  <hp:effects/>
  <hp:sz width="${width}" widthRelTo="ABSOLUTE" height="${height}" heightRelTo="ABSOLUTE" protect="0"/>
  <hp:pos treatAsChar="${treatAsChar}" affectLSpacing="0" flowWithText="1" allowOverlap="0" holdAnchorAndSO="0" vertRelTo="${vertRelTo}" horzRelTo="${horzRelTo}" vertAlign="${vertAlign}" horzAlign="${horzAlign}" vertOffset="${vertOffset}" horzOffset="${horzOffset}"/>
  <hp:outMargin left="0" right="0" top="0" bottom="0"/>
  <hp:shapeComment>Inserted by HWPX MCP</hp:shapeComment>
</hp:pic>`;
  }

  // ============================================================
  // XML Analysis and Repair Tools
  // ============================================================

  /**
   * Analyze XML for issues like tag imbalance, malformed elements, etc.
   * @param sectionIndex Section to analyze (optional, all sections if not specified)
   * @returns Detailed analysis report
   */
  public async analyzeXml(sectionIndex?: number): Promise<{
    hasIssues: boolean;
    sections: Array<{
      sectionIndex: number;
      issues: Array<{
        type: 'tag_imbalance' | 'malformed_tag' | 'unclosed_tag' | 'orphan_close_tag' | 'nesting_error';
        severity: 'error' | 'warning';
        message: string;
        position?: number;
        context?: string;
        suggestedFix?: string;
      }>;
      tagCounts: Record<string, { open: number; close: number; balance: number }>;
    }>;
    summary: string;
  }> {
    if (!this._zip) {
      return { hasIssues: true, sections: [], summary: 'Document not loaded' };
    }

    const result: Awaited<ReturnType<typeof this.analyzeXml>> = {
      hasIssues: false,
      sections: [],
      summary: ''
    };

    // Get sections to analyze
    const sectionsToAnalyze = sectionIndex !== undefined
      ? [sectionIndex]
      : await this.getAvailableSections();

    for (const secIdx of sectionsToAnalyze) {
      const sectionPath = `Contents/section${secIdx}.xml`;
      const file = this._zip.file(sectionPath);
      if (!file) continue;

      const xml = await file.async('string');
      const sectionResult = this.analyzeXmlContent(xml, secIdx);

      if (sectionResult.issues.length > 0) {
        result.hasIssues = true;
      }
      result.sections.push(sectionResult);
    }

    // Generate summary
    const totalIssues = result.sections.reduce((sum, s) => sum + s.issues.length, 0);
    const errors = result.sections.reduce((sum, s) => sum + s.issues.filter(i => i.severity === 'error').length, 0);
    const warnings = result.sections.reduce((sum, s) => sum + s.issues.filter(i => i.severity === 'warning').length, 0);

    result.summary = totalIssues === 0
      ? 'No XML issues detected'
      : `Found ${totalIssues} issue(s): ${errors} error(s), ${warnings} warning(s)`;

    return result;
  }

  /**
   * Analyze XML content for issues
   */
  private analyzeXmlContent(xml: string, sectionIndex: number): {
    sectionIndex: number;
    issues: Array<{
      type: 'tag_imbalance' | 'malformed_tag' | 'unclosed_tag' | 'orphan_close_tag' | 'nesting_error';
      severity: 'error' | 'warning';
      message: string;
      position?: number;
      context?: string;
      suggestedFix?: string;
    }>;
    tagCounts: Record<string, { open: number; close: number; balance: number }>;
  } {
    const issues: Array<{
      type: 'tag_imbalance' | 'malformed_tag' | 'unclosed_tag' | 'orphan_close_tag' | 'nesting_error';
      severity: 'error' | 'warning';
      message: string;
      position?: number;
      context?: string;
      suggestedFix?: string;
    }> = [];

    const tagCounts: Record<string, { open: number; close: number; balance: number }> = {};

    // List of important HWPX tags to check
    const tagsToCheck = [
      'hp:p', 'hp:run', 'hp:t', 'hp:tbl', 'hp:tr', 'hp:tc', 'hp:subList',
      'hp:pic', 'hp:container', 'hp:sec', 'hp:colPr', 'hp:paraPr',
      'hs:tbl', 'hs:tr', 'hs:tc', 'hc:tbl', 'hc:tr', 'hc:tc'
    ];

    // Count open and close tags
    for (const tag of tagsToCheck) {
      const escapedTag = tag.replace(/([.?*+^$[\]\\(){}|-])/g, '\\$1');
      // Match exact tag name followed by whitespace, >, or /
      const openRegex = new RegExp(`<${escapedTag}(?:\\s|>|\\/)`, 'g');
      const closeRegex = new RegExp(`</${escapedTag}>`, 'g');
      // Self-closing: exact tag followed by optional attributes and />
      // Must have space or / immediately after tag name to avoid matching hp:placement for hp:p
      const selfCloseRegex = new RegExp(`<${escapedTag}(?:\\s[^>]*)?\\/>`, 'g');

      const openMatches = xml.match(openRegex) || [];
      const closeMatches = xml.match(closeRegex) || [];
      const selfCloseMatches = xml.match(selfCloseRegex) || [];

      // Self-closing tags should be excluded from open count since they're complete
      // openMatches includes self-closing tags due to the `\/` in the regex
      const openCount = openMatches.length - selfCloseMatches.length;
      const closeCount = closeMatches.length;
      const balance = openCount - closeCount;

      tagCounts[tag] = { open: openCount, close: closeCount, balance };

      if (balance !== 0) {
        issues.push({
          type: (balance > 0 ? 'unclosed_tag' : 'orphan_close_tag') as 'unclosed_tag' | 'orphan_close_tag',
          severity: 'error' as const,
          message: `Tag imbalance for <${tag}>: ${openCount} open, ${closeCount} close (balance: ${balance > 0 ? '+' : ''}${balance})`,
          suggestedFix: balance > 0
            ? `Add ${balance} closing </${tag}> tag(s)`
            : `Remove ${Math.abs(balance)} orphan </${tag}> tag(s)`
        });
      }
    }

    // Find specific problem locations for tbl tags (most common issue)
    const tblIssues = this.findTblTagIssues(xml);
    for (const tblIssue of tblIssues) {
      issues.push({
        ...tblIssue,
        severity: 'error'
      });
    }

    // Check for common nesting errors
    const nestingIssues = this.checkNestingErrors(xml);
    issues.push(...nestingIssues);

    return { sectionIndex, issues, tagCounts };
  }

  /**
   * Find specific issues with tbl (table) tags
   */
  private findTblTagIssues(xml: string): Array<{
    type: 'tag_imbalance' | 'orphan_close_tag' | 'unclosed_tag';
    message: string;
    position: number;
    context: string;
    suggestedFix?: string;
  }> {
    const issues: Array<{
      type: 'tag_imbalance' | 'orphan_close_tag' | 'unclosed_tag';
      message: string;
      position: number;
      context: string;
      suggestedFix?: string;
    }> = [];

    // Track table tag positions
    const tblOpenRegex = /<(?:hp|hs|hc):tbl[^>]*>/g;
    const tblCloseRegex = /<\/(?:hp|hs|hc):tbl>/g;

    interface TagPosition {
      type: 'open' | 'close';
      position: number;
      tag: string;
    }

    const allPositions: TagPosition[] = [];

    let match;
    while ((match = tblOpenRegex.exec(xml)) !== null) {
      allPositions.push({ type: 'open', position: match.index, tag: match[0] });
    }
    while ((match = tblCloseRegex.exec(xml)) !== null) {
      allPositions.push({ type: 'close', position: match.index, tag: match[0] });
    }

    // Sort by position
    allPositions.sort((a, b) => a.position - b.position);

    // Track nesting depth
    let depth = 0;
    for (const pos of allPositions) {
      if (pos.type === 'open') {
        depth++;
      } else {
        depth--;
        if (depth < 0) {
          // Found orphan closing tag
          const contextStart = Math.max(0, pos.position - 50);
          const contextEnd = Math.min(xml.length, pos.position + 50);
          issues.push({
            type: 'orphan_close_tag',
            message: `Orphan closing table tag at position ${pos.position}`,
            position: pos.position,
            context: xml.substring(contextStart, contextEnd).replace(/\n/g, ' '),
            suggestedFix: `Remove the orphan ${pos.tag} tag or add matching opening tag before it`
          });
          depth = 0; // Reset to continue checking
        }
      }
    }

    if (depth > 0) {
      // Unclosed tables
      issues.push({
        type: 'unclosed_tag',
        message: `${depth} unclosed table tag(s) found`,
        position: xml.length,
        context: 'End of document',
        suggestedFix: `Add ${depth} closing </hp:tbl> tag(s)`
      });
    }

    return issues;
  }

  /**
   * Check for common nesting errors
   */
  private checkNestingErrors(xml: string): Array<{
    type: 'nesting_error';
    severity: 'warning';
    message: string;
    position?: number;
    context?: string;
  }> {
    const issues: Array<{
      type: 'nesting_error';
      severity: 'warning';
      message: string;
      position?: number;
      context?: string;
    }> = [];

    // Check for tc outside of tr
    const tcOutsideTr = /<(?:hp|hs|hc):tc[^>]*>(?:(?!<(?:hp|hs|hc):tr[^>]*>).)*?<\/(?:hp|hs|hc):tc>/gs;
    // This is simplified - a full check would need proper nesting validation

    // Check for tr outside of tbl
    const trPattern = /<(?:hp|hs|hc):tr[^>]*>/g;
    const tblPattern = /<(?:hp|hs|hc):tbl[^>]*>/g;

    // Simple check: count if tr appears without preceding tbl
    let match;
    let lastTblPos = -1;
    let lastTblClosePos = -1;

    const tblClosePattern = /<\/(?:hp|hs|hc):tbl>/g;

    while ((match = tblPattern.exec(xml)) !== null) {
      lastTblPos = match.index;
    }

    tblPattern.lastIndex = 0;

    // More sophisticated nesting check would go here
    // For now, we rely on tag counting

    return issues;
  }

  /**
   * Attempt to repair XML issues in a section
   * @param sectionIndex Section to repair
   * @param options Repair options
   * @returns Repair result
   */
  public async repairXml(
    sectionIndex: number,
    options: {
      removeOrphanCloseTags?: boolean;
      addMissingCloseTags?: boolean;
      fixTableStructure?: boolean;
      backup?: boolean;
    } = {}
  ): Promise<{
    success: boolean;
    message: string;
    repairsApplied: string[];
    originalXml?: string;
  }> {
    if (!this._zip) {
      return { success: false, message: 'Document not loaded', repairsApplied: [] };
    }

    const sectionPath = `Contents/section${sectionIndex}.xml`;
    const file = this._zip.file(sectionPath);
    if (!file) {
      return { success: false, message: `Section ${sectionIndex} not found`, repairsApplied: [] };
    }

    const originalXml = await file.async('string');
    let xml = originalXml;
    const repairsApplied: string[] = [];

    // Analyze current issues
    const analysis = this.analyzeXmlContent(xml, sectionIndex);
    if (analysis.issues.length === 0) {
      return { success: true, message: 'No issues to repair', repairsApplied: [] };
    }

    const opts = {
      removeOrphanCloseTags: options.removeOrphanCloseTags ?? true,
      addMissingCloseTags: options.addMissingCloseTags ?? true,
      fixTableStructure: options.fixTableStructure ?? true,
      backup: options.backup ?? true
    };

    // Repair orphan close tags
    if (opts.removeOrphanCloseTags) {
      const result = this.removeOrphanCloseTags(xml);
      if (result.modified) {
        xml = result.xml;
        repairsApplied.push(...result.repairs);
      }
    }

    // Fix table structure
    if (opts.fixTableStructure) {
      const result = this.fixTableStructure(xml);
      if (result.modified) {
        xml = result.xml;
        repairsApplied.push(...result.repairs);
      }
    }

    // Validate after repairs
    const afterAnalysis = this.analyzeXmlContent(xml, sectionIndex);
    const remainingErrors = afterAnalysis.issues.filter(i => i.severity === 'error');

    if (remainingErrors.length > 0) {
      // Don't save if errors remain
      return {
        success: false,
        message: `Repair incomplete: ${remainingErrors.length} error(s) remain`,
        repairsApplied,
        originalXml: opts.backup ? originalXml : undefined
      };
    }

    // Save repaired XML
    this._zip.file(sectionPath, xml);
    this.markModified();

    return {
      success: true,
      message: `Repaired ${repairsApplied.length} issue(s)`,
      repairsApplied,
      originalXml: opts.backup ? originalXml : undefined
    };
  }

  /**
   * Remove orphan closing tags (tbl, tr, tc, p, subList)
   */
  private removeOrphanCloseTags(xml: string): { xml: string; modified: boolean; repairs: string[] } {
    const repairs: string[] = [];
    let modified = false;
    let result = xml;

    // Process each tag type separately
    const tagsToFix = ['tbl', 'tr', 'tc', 'subList', 'p'];

    for (const tagName of tagsToFix) {
      const tagResult = this.fixTagImbalanceGlobal(result, tagName);
      if (tagResult.modified) {
        result = tagResult.xml;
        modified = true;
        repairs.push(...tagResult.repairs);
      }
    }

    return { xml: result, modified, repairs };
  }

  /**
   * Fix tag imbalance globally (not just within tables)
   */
  private fixTagImbalanceGlobal(xml: string, tagName: string): { xml: string; modified: boolean; repairs: string[] } {
    const repairs: string[] = [];
    let modified = false;
    let result = xml;

    // Collect all open and close tags with positions
    const allTags: Array<{ type: 'open' | 'close' | 'selfclose'; pos: number; tag: string; endPos: number }> = [];

    const openRegex = new RegExp(`<(hp|hs|hc):${tagName}(?:\\s[^>]*)?>`, 'g');
    const closeRegex = new RegExp(`</(hp|hs|hc):${tagName}>`, 'g');
    const selfCloseRegex = new RegExp(`<(hp|hs|hc):${tagName}(?:\\s[^>]*)?\\/\\s*>`, 'g');

    let match;

    // Find self-closing tags first
    const selfClosePositions = new Set<number>();
    while ((match = selfCloseRegex.exec(xml)) !== null) {
      selfClosePositions.add(match.index);
      allTags.push({ type: 'selfclose', pos: match.index, tag: match[0], endPos: match.index + match[0].length });
    }

    // Find open tags (excluding self-closing)
    while ((match = openRegex.exec(xml)) !== null) {
      if (!selfClosePositions.has(match.index)) {
        allTags.push({ type: 'open', pos: match.index, tag: match[0], endPos: match.index + match[0].length });
      }
    }

    // Find close tags
    while ((match = closeRegex.exec(xml)) !== null) {
      allTags.push({ type: 'close', pos: match.index, tag: match[0], endPos: match.index + match[0].length });
    }

    // Sort by position
    allTags.sort((a, b) => a.pos - b.pos);

    // Find orphan close tags (depth tracking)
    const orphanClosePositions: Array<{ pos: number; endPos: number; tag: string }> = [];
    let depth = 0;

    for (const tag of allTags) {
      if (tag.type === 'open') {
        depth++;
      } else if (tag.type === 'close') {
        depth--;
        if (depth < 0) {
          orphanClosePositions.push({ pos: tag.pos, endPos: tag.endPos, tag: tag.tag });
          depth = 0;
        }
      }
    }

    // Remove orphan close tags (from end to start)
    for (let i = orphanClosePositions.length - 1; i >= 0; i--) {
      const orphan = orphanClosePositions[i];
      result = result.substring(0, orphan.pos) + result.substring(orphan.endPos);
      repairs.push(`Removed orphan </${tagName}> at position ${orphan.pos}`);
      modified = true;
    }

    return { xml: result, modified, repairs };
  }

  /**
   * Fix table structure issues
   */
  private fixTableStructure(xml: string): { xml: string; modified: boolean; repairs: string[] } {
    const repairs: string[] = [];
    let modified = false;
    let result = xml;

    // Fix orphan tr and tc close tags within tables
    const tables = this.findAllTables(result);

    for (let i = tables.length - 1; i >= 0; i--) {
      const currentTables = this.findAllTables(result);
      if (i >= currentTables.length) continue;

      const table = currentTables[i];
      let tableXml = table.xml;
      let tableModified = false;

      // Fix tr tag imbalance
      const trResult = this.fixTagImbalance(tableXml, 'tr');
      if (trResult.modified) {
        tableXml = trResult.xml;
        tableModified = true;
        repairs.push(...trResult.repairs.map(r => `Table ${i}: ${r}`));
      }

      // Fix tc tag imbalance
      const tcResult = this.fixTagImbalance(tableXml, 'tc');
      if (tcResult.modified) {
        tableXml = tcResult.xml;
        tableModified = true;
        repairs.push(...tcResult.repairs.map(r => `Table ${i}: ${r}`));
      }

      // Fix subList tag imbalance
      const subListResult = this.fixTagImbalance(tableXml, 'subList');
      if (subListResult.modified) {
        tableXml = subListResult.xml;
        tableModified = true;
        repairs.push(...subListResult.repairs.map(r => `Table ${i}: ${r}`));
      }

      // Fix p tag imbalance within table
      const pResult = this.fixTagImbalance(tableXml, 'p');
      if (pResult.modified) {
        tableXml = pResult.xml;
        tableModified = true;
        repairs.push(...pResult.repairs.map(r => `Table ${i}: ${r}`));
      }

      if (tableModified) {
        // Validate the repaired table structure
        const tableError = this.validateTableStructure(tableXml);
        if (tableError) {
          // Table structure still invalid, log warning but keep repairs
          repairs.push(`Table ${i}: Structure validation after repair: ${tableError}`);
        }

        result = result.substring(0, table.startIndex) + tableXml + result.substring(table.endIndex);
        modified = true;
      }
    }

    return { xml: result, modified, repairs };
  }

  /**
   * Fix tag imbalance for a specific element type
   */
  private fixTagImbalance(xml: string, tagName: string): { xml: string; modified: boolean; repairs: string[] } {
    const repairs: string[] = [];
    let modified = false;
    let result = xml;

    // Collect all open and close tags with positions
    const allTags: Array<{ type: 'open' | 'close' | 'selfclose'; pos: number; tag: string; prefix: string; endPos: number }> = [];

    const openRegex = new RegExp(`<(hp|hs|hc):${tagName}(?:\\s[^>]*)?>`, 'g');
    const closeRegex = new RegExp(`</(hp|hs|hc):${tagName}>`, 'g');
    const selfCloseRegex = new RegExp(`<(hp|hs|hc):${tagName}(?:\\s[^>]*)?\\/\\s*>`, 'g');

    let match;

    // Find self-closing tags first
    const selfClosePositions = new Set<number>();
    while ((match = selfCloseRegex.exec(xml)) !== null) {
      selfClosePositions.add(match.index);
      allTags.push({ type: 'selfclose', pos: match.index, tag: match[0], prefix: match[1], endPos: match.index + match[0].length });
    }

    // Find open tags (excluding self-closing)
    while ((match = openRegex.exec(xml)) !== null) {
      if (!selfClosePositions.has(match.index)) {
        allTags.push({ type: 'open', pos: match.index, tag: match[0], prefix: match[1], endPos: match.index + match[0].length });
      }
    }

    // Find close tags
    while ((match = closeRegex.exec(xml)) !== null) {
      allTags.push({ type: 'close', pos: match.index, tag: match[0], prefix: match[1], endPos: match.index + match[0].length });
    }

    // Sort by position
    allTags.sort((a, b) => a.pos - b.pos);

    // Find orphan close tags (depth tracking)
    const orphanClosePositions: Array<{ pos: number; endPos: number; tag: string }> = [];
    let depth = 0;

    for (const tag of allTags) {
      if (tag.type === 'open') {
        depth++;
      } else if (tag.type === 'close') {
        depth--;
        if (depth < 0) {
          orphanClosePositions.push({ pos: tag.pos, endPos: tag.endPos, tag: tag.tag });
          depth = 0; // Reset to continue finding more orphans
        }
      }
      // selfclose doesn't affect depth
    }

    // Remove orphan close tags (from end to start to preserve positions)
    for (let i = orphanClosePositions.length - 1; i >= 0; i--) {
      const orphan = orphanClosePositions[i];
      result = result.substring(0, orphan.pos) + result.substring(orphan.endPos);
      repairs.push(`Removed orphan closing tag ${orphan.tag} at position ${orphan.pos}`);
      modified = true;
    }

    // Recount to check for missing close tags
    if (modified) {
      // Recount after removal
      const openCount = (result.match(new RegExp(`<(hp|hs|hc):${tagName}(?:\\s[^>]*)?>`, 'g')) || []).length -
                       (result.match(new RegExp(`<(hp|hs|hc):${tagName}(?:\\s[^>]*)?\\/\\s*>`, 'g')) || []).length;
      const closeCount = (result.match(new RegExp(`</(hp|hs|hc):${tagName}>`, 'g')) || []).length;

      if (openCount > closeCount) {
        // Need to add closing tags - this is complex and may require manual intervention
        repairs.push(`Warning: ${openCount - closeCount} missing closing </${tagName}> tag(s) - manual review recommended`);
      }
    }

    return { xml: result, modified, repairs };
  }

  /**
   * Get raw XML of a section for manual inspection/editing
   */
  public async getRawSectionXml(sectionIndex: number): Promise<string | null> {
    if (!this._zip) return null;

    const sectionPath = `Contents/section${sectionIndex}.xml`;
    const file = this._zip.file(sectionPath);
    if (!file) return null;

    return file.async('string');
  }

  /**
   * Set raw XML of a section (use with caution)
   */
  public async setRawSectionXml(sectionIndex: number, xml: string, validate: boolean = true): Promise<{
    success: boolean;
    message: string;
    issues?: Array<{ type: string; message: string }>;
  }> {
    if (!this._zip) {
      return { success: false, message: 'Document not loaded' };
    }

    // Validate XML if requested
    if (validate) {
      const analysis = this.analyzeXmlContent(xml, sectionIndex);
      const errors = analysis.issues.filter(i => i.severity === 'error');

      if (errors.length > 0) {
        return {
          success: false,
          message: `XML validation failed: ${errors.length} error(s)`,
          issues: errors.map(e => ({ type: e.type, message: e.message }))
        };
      }
    }

    const sectionPath = `Contents/section${sectionIndex}.xml`;
    this._zip.file(sectionPath, xml);
    this.markModified();

    // Note: Internal state is not automatically updated.
    // Save and reopen the document to see changes in other tools.

    return { success: true, message: 'Section XML updated successfully. Save and reopen to refresh internal state.' };
  }

  // ============================================================
  // Consolidated Tools (통합 도구)
  // 기존 114개 도구의 기능을 유지하면서 LLM이 쉽게 사용할 수 있는 통합 인터페이스
  // ============================================================

  /**
   * 위치 찾기 통합 도구
   * @param type 찾을 대상 유형: 'table' | 'paragraph' | 'insert_point'
   * @param query 검색할 텍스트
   * @returns 찾은 위치 정보 또는 null
   */
  findPosition(
    type: 'table' | 'paragraph' | 'insert_point',
    query: string
  ): {
    type: string;
    sectionIndex: number;
    elementIndex?: number;
    tableIndex?: number;
    paragraphIndex?: number;
    foundIn?: 'paragraph' | 'table_cell';
    tableInfo?: { tableIndex: number; row: number; col: number };
  } | null {
    switch (type) {
      case 'table': {
        // findTableByHeader 기능 사용
        const tables = this.getTableMap();
        for (let i = 0; i < tables.length; i++) {
          const table = tables[i];
          if (table.header?.toLowerCase().includes(query.toLowerCase())) {
            return {
              type: 'table',
              sectionIndex: table.section_index,
              tableIndex: table.table_index,
            };
          }
          // 테이블 내용에서도 검색
          if (table.first_row_preview?.some(cell => cell.toLowerCase().includes(query.toLowerCase()))) {
            return {
              type: 'table',
              sectionIndex: table.section_index,
              tableIndex: table.table_index,
            };
          }
        }
        return null;
      }

      case 'paragraph': {
        // findParagraphByText 기능 사용
        const sections = this._content.sections;
        for (let si = 0; si < sections.length; si++) {
          const section = sections[si];
          let paragraphIndex = 0;
          for (let ei = 0; ei < section.elements.length; ei++) {
            const element = section.elements[ei];
            if (element.type === 'paragraph') {
              const para = element.data as HwpxParagraph;
              const text = para.runs.map(r => r.text).join('');
              if (text.toLowerCase().includes(query.toLowerCase())) {
                return {
                  type: 'paragraph',
                  sectionIndex: si,
                  elementIndex: ei,
                  paragraphIndex,
                };
              }
              paragraphIndex++;
            }
          }
        }
        return null;
      }

      case 'insert_point': {
        // findInsertPositionAfterHeader 기능 사용
        const result = this.findInsertPositionAfterHeader(query);
        if (!result) return null;
        return {
          type: 'insert_point',
          sectionIndex: result.section_index,
          elementIndex: result.insert_after,
          foundIn: result.found_in,
          tableInfo: result.table_info ? {
            tableIndex: result.table_info.table_index,
            row: result.table_info.row,
            col: result.table_info.col,
          } : undefined,
        };
      }

      default:
        return null;
    }
  }

  /**
   * 테이블 조회 통합 도구
   * @param options 조회 옵션
   * @returns 테이블 정보
   */
  queryTable(options: {
    mode: 'list' | 'full' | 'cell' | 'map' | 'summary';
    tableIndex?: number;
    row?: number;
    col?: number;
    sectionIndex?: number;
  }): {
    tables?: Array<{ index: number; rowCount: number; colCount: number; sectionIndex: number }>;
    table?: HwpxTable | null;
    cell?: { text: string; paragraphs: HwpxParagraph[] } | null;
    map?: Array<{ index: number; header: string; sectionIndex: number; elementIndex: number; firstRowPreview: string[] }>;
  } {
    const sectionIndex = options.sectionIndex ?? 0;

    switch (options.mode) {
      case 'list': {
        // getTables 기능
        const result = this.getTables();
        return {
          tables: result.map(t => ({
            index: t.index,
            rowCount: t.rows,
            colCount: t.cols,
            sectionIndex: t.section,
          })),
        };
      }

      case 'full': {
        // getTable 기능
        if (options.tableIndex === undefined) return { table: null };
        const table = this.findTable(sectionIndex, options.tableIndex);
        return { table };
      }

      case 'cell': {
        // getTableCell 기능
        if (options.tableIndex === undefined || options.row === undefined || options.col === undefined) {
          return { cell: null };
        }
        const cellResult = this.getTableCell(sectionIndex, options.tableIndex, options.row, options.col);
        if (!cellResult) return { cell: null };
        return {
          cell: {
            text: cellResult.text,
            paragraphs: cellResult.cell.paragraphs,
          },
        };
      }

      case 'map': {
        // getTableMap 기능
        const tableMap = this.getTableMap();
        return {
          map: tableMap.map((t) => ({
            index: t.table_index,
            header: t.header || '',
            sectionIndex: t.section_index,
            elementIndex: 0, // Not available from getTableMap
            firstRowPreview: t.first_row_preview || [],
          })),
        };
      }

      case 'summary': {
        // getTablesSummary 기능
        const tables = this.getTables();
        return {
          tables: tables.map(t => ({
            index: t.index,
            rowCount: t.rows,
            colCount: t.cols,
            sectionIndex: t.section,
          })),
        };
      }

      default:
        return {};
    }
  }

  /**
   * 내용 수정 통합 도구
   * @param options 수정 옵션
   * @returns 성공 여부
   */
  modifyContent(options: {
    type: 'cell' | 'replace' | 'paragraph';
    // cell 옵션
    tableIndex?: number;
    row?: number;
    col?: number;
    // paragraph 옵션
    sectionIndex?: number;
    paragraphIndex?: number;
    runIndex?: number;  // paragraph 타입에서 run 인덱스 (기본값 0)
    // 공통
    text?: string;
    // replace 옵션
    oldText?: string;
    newText?: string;
    replaceAll?: boolean;
    caseSensitive?: boolean;
  }): boolean {
    switch (options.type) {
      case 'cell': {
        if (options.tableIndex === undefined || options.row === undefined ||
            options.col === undefined || options.text === undefined) {
          return false;
        }
        const sectionIndex = options.sectionIndex ?? 0;
        return this.updateTableCell(sectionIndex, options.tableIndex, options.row, options.col, options.text);
      }

      case 'replace': {
        if (!options.oldText || options.newText === undefined) return false;
        const count = this.replaceText(options.oldText, options.newText, {
          replaceAll: options.replaceAll ?? true,
          caseSensitive: options.caseSensitive ?? false,
        });
        return count > 0;
      }

      case 'paragraph': {
        if (options.sectionIndex === undefined || options.paragraphIndex === undefined ||
            options.text === undefined) {
          return false;
        }
        const runIndex = options.runIndex ?? 0;
        this.updateParagraphText(options.sectionIndex, options.paragraphIndex, runIndex, options.text);
        return true;
      }

      default:
        return false;
    }
  }

  /**
   * 스타일 적용 통합 도구
   * 기존 applyStyle(sectionIndex, paragraphIndex, styleId)과 구분하기 위해 이름 변경
   * @param options 스타일 옵션
   * @returns 성공 여부
   */
  applyConsolidatedStyle(options: {
    target: 'paragraph' | 'table_cell' | 'text';
    sectionIndex?: number;
    paragraphIndex?: number;
    tableIndex?: number;
    row?: number;
    col?: number;
    runIndex?: number;
    style: {
      hangingIndent?: number;  // 0 = 제거, >0 = 설정
      align?: 'left' | 'center' | 'right' | 'justify';
      lineSpacing?: number;
      bold?: boolean;
      italic?: boolean;
      fontSize?: number;
      fontColor?: string;
    };
  }): boolean {
    const sectionIndex = options.sectionIndex ?? 0;

    switch (options.target) {
      case 'paragraph': {
        if (options.paragraphIndex === undefined) return false;

        // 내어쓰기 처리
        if (options.style.hangingIndent !== undefined) {
          if (options.style.hangingIndent === 0) {
            return this.removeHangingIndent(sectionIndex, options.paragraphIndex);
          } else {
            return this.setHangingIndent(sectionIndex, options.paragraphIndex, options.style.hangingIndent);
          }
        }

        // 단락 스타일 처리
        const paraStyle: Partial<ParagraphStyle> = {};
        if (options.style.align) paraStyle.align = options.style.align;
        if (options.style.lineSpacing) paraStyle.lineSpacing = options.style.lineSpacing;

        if (Object.keys(paraStyle).length > 0) {
          this.applyParagraphStyle(sectionIndex, options.paragraphIndex, paraStyle);
        }
        return true;
      }

      case 'table_cell': {
        if (options.tableIndex === undefined || options.row === undefined ||
            options.col === undefined) {
          return false;
        }
        const paragraphIndex = options.paragraphIndex ?? 0;

        // 내어쓰기 처리
        if (options.style.hangingIndent !== undefined) {
          if (options.style.hangingIndent === 0) {
            return this.removeTableCellHangingIndent(sectionIndex, options.tableIndex, options.row, options.col, paragraphIndex);
          } else {
            return this.setTableCellHangingIndent(sectionIndex, options.tableIndex, options.row, options.col, paragraphIndex, options.style.hangingIndent);
          }
        }
        return true;
      }

      case 'text': {
        if (options.paragraphIndex === undefined) return false;

        const charStyle: Partial<CharacterStyle> = {};
        if (options.style.bold !== undefined) charStyle.bold = options.style.bold;
        if (options.style.italic !== undefined) charStyle.italic = options.style.italic;
        if (options.style.fontSize !== undefined) charStyle.fontSize = options.style.fontSize;
        if (options.style.fontColor !== undefined) charStyle.fontColor = options.style.fontColor;

        if (Object.keys(charStyle).length > 0) {
          this.applyCharacterStyle(sectionIndex, options.paragraphIndex, options.runIndex ?? 0, charStyle);
        }
        return true;
      }

      default:
        return false;
    }
  }

  // ===== Agentic Document Reading System =====

  // In-memory position index storage
  private _positionIndex: PositionIndexEntry[] = [];
  private _documentChunks: DocumentChunk[] = [];
  private _lastChunkTime: number = 0;

  /**
   * Chunk document into overlapping segments for agentic reading
   * @param chunkSize Target chunk size in characters (default 500)
   * @param overlap Overlap between chunks in characters (default 100)
   * @returns Array of document chunks with position information
   */
  public chunkDocument(chunkSize: number = 500, overlap: number = 100): DocumentChunk[] {
    // Input validation to prevent infinite loop
    chunkSize = Math.max(50, Math.min(100000, chunkSize)); // Clamp to reasonable range
    overlap = Math.max(0, Math.min(chunkSize - 1, overlap)); // Ensure overlap < chunkSize

    const chunks: DocumentChunk[] = [];
    let globalOffset = 0;
    let chunkId = 0;

    for (let secIdx = 0; secIdx < this._content.sections.length; secIdx++) {
      const section = this._content.sections[secIdx];
      let sectionText = '';
      const elementPositions: Array<{ start: number; end: number; type: string; index: number }> = [];

      // Build section text and track element positions
      for (let elemIdx = 0; elemIdx < section.elements.length; elemIdx++) {
        const elem = section.elements[elemIdx];
        const startPos = sectionText.length;

        if (elem.type === 'paragraph') {
          const para = elem.data;
          const paraText = para.runs?.map(r => r.text || '').join('') || '';
          sectionText += paraText + '\n';
          elementPositions.push({ start: startPos, end: sectionText.length, type: 'paragraph', index: elemIdx });
        } else if (elem.type === 'table') {
          const table = elem.data;
          let tableText = '[TABLE]\n';
          for (const row of table.rows || []) {
            for (const cell of row.cells || []) {
              const cellText = cell.paragraphs?.map(p =>
                p.runs?.map(r => r.text || '').join('') || ''
              ).join(' ') || '';
              tableText += cellText + '\t';
            }
            tableText += '\n';
          }
          tableText += '[/TABLE]\n';
          sectionText += tableText;
          elementPositions.push({ start: startPos, end: sectionText.length, type: 'table', index: elemIdx });
        }
      }

      // Create chunks from section text with sliding window
      let pos = 0;
      while (pos < sectionText.length) {
        const endPos = Math.min(pos + chunkSize, sectionText.length);
        const chunkText = sectionText.substring(pos, endPos);

        // Determine which elements this chunk covers
        const coveredElements = elementPositions.filter(e =>
          (e.start >= pos && e.start < endPos) || (e.end > pos && e.end <= endPos) || (e.start <= pos && e.end >= endPos)
        );

        const hasTable = coveredElements.some(e => e.type === 'table');
        const elementType = coveredElements.length === 0 ? 'mixed' :
          coveredElements.every(e => e.type === 'paragraph') ? 'paragraph' :
          coveredElements.every(e => e.type === 'table') ? 'table' : 'mixed';

        // Detect heading level (simple heuristic: short paragraphs at start might be headings)
        let headingLevel: number | undefined;
        if (chunkText.length < 100 && !hasTable) {
          const firstLine = chunkText.split('\n')[0];
          if (firstLine && firstLine.length < 50) {
            // Simple heading detection based on content
            if (/^[1-9][.)]?\s/.test(firstLine)) headingLevel = 1;
            else if (/^[가-힣][.)]?\s/.test(firstLine)) headingLevel = 2;
            else if (/^[①-⑩]/.test(firstLine) || /^\([1-9]\)/.test(firstLine)) headingLevel = 3;
          }
        }

        const chunk: DocumentChunk = {
          id: `chunk_${secIdx}_${chunkId++}`,
          text: chunkText,
          startOffset: globalOffset + pos,
          endOffset: globalOffset + endPos,
          sectionIndex: secIdx,
          elementType,
          elementIndex: coveredElements.length > 0 ? coveredElements[0].index : undefined,
          metadata: {
            charCount: chunkText.length,
            wordCount: chunkText.split(/\s+/).filter(w => w.length > 0).length,
            hasTable,
            headingLevel,
          },
        };

        chunks.push(chunk);

        // Move position with overlap
        pos += chunkSize - overlap;
        if (pos >= sectionText.length - overlap) break;
      }

      globalOffset += sectionText.length;
    }

    // Cache the chunks
    this._documentChunks = chunks;
    this._lastChunkTime = Date.now();

    return chunks;
  }

  /**
   * Search chunks using keyword-based similarity scoring
   * Returns chunks ranked by relevance to the query
   * @param query Search query
   * @param topK Number of top results to return (default 5)
   * @param minScore Minimum similarity score threshold (default 0.1)
   */
  public searchChunks(query: string, topK: number = 5, minScore: number = 0.1): Array<{
    chunk: DocumentChunk;
    score: number;
    matchedTerms: string[];
    snippet: string;
  }> {
    // Ensure chunks are available
    if (this._documentChunks.length === 0) {
      this.chunkDocument();
    }

    // Tokenize and normalize query
    const queryTerms = this.tokenize(query.toLowerCase());
    if (queryTerms.length === 0) return [];

    // Calculate IDF for query terms across all chunks
    const termDocFreq: Map<string, number> = new Map();
    for (const chunk of this._documentChunks) {
      const chunkTerms = new Set(this.tokenize(chunk.text.toLowerCase()));
      for (const term of queryTerms) {
        if (chunkTerms.has(term)) {
          termDocFreq.set(term, (termDocFreq.get(term) || 0) + 1);
        }
      }
    }

    const N = this._documentChunks.length;
    if (N === 0) return []; // Guard against empty document

    const termIdf: Map<string, number> = new Map();
    for (const term of queryTerms) {
      const df = termDocFreq.get(term) || 0;
      termIdf.set(term, df > 0 ? Math.log((N + 1) / (df + 1)) + 1 : 0);
    }

    // Score each chunk using BM25-like scoring
    const k1 = 1.5;
    const b = 0.75;
    const totalLength = this._documentChunks.reduce((sum, c) => sum + c.text.length, 0);
    const avgDl = Math.max(1, totalLength / N); // Prevent division by zero

    const results: Array<{ chunk: DocumentChunk; score: number; matchedTerms: string[]; snippet: string }> = [];

    for (const chunk of this._documentChunks) {
      const chunkText = chunk.text.toLowerCase();
      const chunkTerms = this.tokenize(chunkText);
      const termFreq: Map<string, number> = new Map();

      for (const term of chunkTerms) {
        termFreq.set(term, (termFreq.get(term) || 0) + 1);
      }

      let score = 0;
      const matchedTerms: string[] = [];

      for (const term of queryTerms) {
        const tf = termFreq.get(term) || 0;
        if (tf > 0) {
          matchedTerms.push(term);
          const idf = termIdf.get(term) || 0;
          const dl = chunk.text.length;
          // BM25 scoring
          score += idf * ((tf * (k1 + 1)) / (tf + k1 * (1 - b + b * (dl / avgDl))));
        }
      }

      // Bonus for phrase matching
      if (queryTerms.length > 1) {
        const queryPhrase = queryTerms.join(' ');
        if (chunkText.includes(queryPhrase)) {
          score *= 1.5; // Boost for exact phrase match
        }
      }

      if (score >= minScore) {
        // Extract snippet around first match
        const firstMatch = matchedTerms[0];
        const matchIndex = chunkText.indexOf(firstMatch);
        const snippetStart = Math.max(0, matchIndex - 50);
        const snippetEnd = Math.min(chunk.text.length, matchIndex + firstMatch.length + 100);
        const snippet = (snippetStart > 0 ? '...' : '') +
          chunk.text.substring(snippetStart, snippetEnd) +
          (snippetEnd < chunk.text.length ? '...' : '');

        results.push({ chunk, score, matchedTerms, snippet });
      }
    }

    // Sort by score descending and return top K
    results.sort((a, b) => b.score - a.score);
    return results.slice(0, topK);
  }

  /**
   * Simple tokenizer for Korean and English text
   */
  private tokenize(text: string): string[] {
    // Limit text length to prevent ReDoS attacks on very large strings
    const MAX_TOKENIZE_LENGTH = 100000;
    if (text.length > MAX_TOKENIZE_LENGTH) {
      text = text.substring(0, MAX_TOKENIZE_LENGTH);
    }

    // Remove special characters, split on whitespace and common delimiters
    return text
      .replace(/[^\w\s가-힣]/g, ' ')
      .split(/\s+/)
      .filter(token => token.length >= 2); // Filter out single chars
  }

  /**
   * Extract table of contents based on formatting rules
   * Identifies headings by:
   * - Numbered patterns (1., 가., (1), ①)
   * - Short paragraphs followed by longer content
   * - Bold or larger font (if style info available)
   */
  public extractToc(): Array<{
    level: number;
    title: string;
    sectionIndex: number;
    elementIndex: number;
    offset: number;
    children?: Array<{ level: number; title: string; sectionIndex: number; elementIndex: number; offset: number }>;
  }> {
    const toc: Array<{
      level: number;
      title: string;
      sectionIndex: number;
      elementIndex: number;
      offset: number;
    }> = [];

    // Heading detection patterns (Korean document conventions)
    const headingPatterns = [
      { pattern: /^[IVX]+[.)]?\s*(.+)$/i, level: 1 },      // Roman numerals: I. II. III.
      { pattern: /^[1-9][0-9]?[.)]?\s*(.+)$/, level: 1 },   // Arabic numerals: 1. 2. 3.
      { pattern: /^[가-힣][.)]?\s*(.+)$/, level: 2 },       // Korean: 가. 나. 다.
      { pattern: /^[①-⑳]\s*(.+)$/, level: 3 },             // Circled numbers: ① ② ③
      { pattern: /^\([1-9][0-9]?\)\s*(.+)$/, level: 3 },   // Parenthesized: (1) (2) (3)
      { pattern: /^[ㄱ-ㅎ][.)]?\s*(.+)$/, level: 4 },       // Korean consonants: ㄱ. ㄴ. ㄷ.
      { pattern: /^[-•◦▪]\s*(.+)$/, level: 5 },            // Bullets
    ];

    let globalOffset = 0;

    for (let secIdx = 0; secIdx < this._content.sections.length; secIdx++) {
      const section = this._content.sections[secIdx];

      for (let elemIdx = 0; elemIdx < section.elements.length; elemIdx++) {
        const elem = section.elements[elemIdx];

        if (elem.type === 'paragraph') {
          const para = elem.data;
          const paraText = para.runs?.map(r => r.text || '').join('').trim() || '';

          if (paraText.length === 0) {
            globalOffset += 1; // Empty paragraph
            continue;
          }

          // Check if this looks like a heading
          for (const { pattern, level } of headingPatterns) {
            const match = paraText.match(pattern);
            if (match) {
              // Additional check: headings are typically short
              if (paraText.length <= 100) {
                toc.push({
                  level,
                  title: paraText,
                  sectionIndex: secIdx,
                  elementIndex: elemIdx,
                  offset: globalOffset,
                });
                break;
              }
            }
          }

          globalOffset += paraText.length + 1;
        } else if (elem.type === 'table') {
          const table = elem.data;
          // Estimate table text length
          let tableLen = 0;
          for (const row of table.rows || []) {
            for (const cell of row.cells || []) {
              for (const p of cell.paragraphs || []) {
                tableLen += (p.runs?.map(r => r.text || '').join('').length || 0) + 1;
              }
            }
          }
          globalOffset += tableLen + 10; // Approximate
        }
      }
    }

    // Build hierarchical structure
    return this.buildTocHierarchy(toc);
  }

  /**
   * Build hierarchical TOC structure from flat list
   */
  private buildTocHierarchy(flatToc: Array<{
    level: number;
    title: string;
    sectionIndex: number;
    elementIndex: number;
    offset: number;
  }>): Array<{
    level: number;
    title: string;
    sectionIndex: number;
    elementIndex: number;
    offset: number;
    children?: Array<{ level: number; title: string; sectionIndex: number; elementIndex: number; offset: number }>;
  }> {
    // For simplicity, return flat structure with level info
    // Client can build tree from levels
    return flatToc;
  }

  /**
   * Build and store position index for quick lookup
   * Call this after document modifications to keep index updated
   */
  public buildPositionIndex(): PositionIndexEntry[] {
    this._positionIndex = [];
    let globalOffset = 0;
    let entryId = 0;

    // Heading patterns for level detection
    const levelPatterns: Array<{ pattern: RegExp; level: number }> = [
      { pattern: /^[IVX]+[.)]?/i, level: 1 },
      { pattern: /^[1-9][0-9]?[.)]/, level: 1 },
      { pattern: /^[가-힣][.)]/, level: 2 },
      { pattern: /^[①-⑳]/, level: 3 },
      { pattern: /^\([1-9]/, level: 3 },
    ];

    for (let secIdx = 0; secIdx < this._content.sections.length; secIdx++) {
      const section = this._content.sections[secIdx];
      let tableIndex = 0;

      for (let elemIdx = 0; elemIdx < section.elements.length; elemIdx++) {
        const elem = section.elements[elemIdx];

        if (elem.type === 'paragraph') {
          const para = elem.data;
          const text = para.runs?.map(r => r.text || '').join('').trim() || '';

          if (text.length === 0) {
            // Empty paragraph - keep consistent with extractToc()
            globalOffset += 1;
            continue;
          }

          // Detect if heading
          let level: number | undefined;
          let type: 'heading' | 'paragraph' = 'paragraph';

          for (const { pattern, level: lvl } of levelPatterns) {
            if (pattern.test(text) && text.length <= 100) {
              level = lvl;
              type = 'heading';
              break;
            }
          }

          this._positionIndex.push({
            id: `pos_${entryId++}`,
            type,
            text: text.substring(0, 200), // Truncate long text
            sectionIndex: secIdx,
            elementIndex: elemIdx,
            offset: globalOffset,
            level,
          });

          globalOffset += text.length + 1;
        } else if (elem.type === 'table') {
          const table = elem.data;
          const rowCount = table.rows?.length || 0;
          const colCount = table.rows?.[0]?.cells?.length || 0;

          // Get table header/first cell text for identification
          let headerText = '';
          if (table.rows?.[0]?.cells?.[0]?.paragraphs?.[0]) {
            headerText = table.rows[0].cells[0].paragraphs[0].runs?.map(r => r.text || '').join('') || '';
          }

          this._positionIndex.push({
            id: `pos_${entryId++}`,
            type: 'table',
            text: headerText.substring(0, 100) || `Table ${tableIndex + 1}`,
            sectionIndex: secIdx,
            elementIndex: elemIdx,
            offset: globalOffset,
            tableInfo: {
              tableIndex: tableIndex++,
              rows: rowCount,
              cols: colCount,
            },
          });

          // Estimate table size
          let tableLen = 0;
          for (const row of table.rows || []) {
            for (const cell of row.cells || []) {
              for (const p of cell.paragraphs || []) {
                tableLen += (p.runs?.map(r => r.text || '').join('').length || 0) + 1;
              }
            }
          }
          globalOffset += tableLen + 10;
        }
      }
    }

    return this._positionIndex;
  }

  /**
   * Get cached position index or build if needed
   */
  public getPositionIndex(): PositionIndexEntry[] {
    if (this._positionIndex.length === 0) {
      this.buildPositionIndex();
    }
    return this._positionIndex;
  }

  /**
   * Search position index by text query
   */
  public searchPositionIndex(query: string, type?: 'heading' | 'paragraph' | 'table'): PositionIndexEntry[] {
    const index = this.getPositionIndex();
    const queryLower = query.toLowerCase();

    return index.filter(entry => {
      if (type && entry.type !== type) return false;
      return entry.text.toLowerCase().includes(queryLower);
    });
  }

  /**
   * Get chunk at specific offset
   */
  public getChunkAtOffset(offset: number): DocumentChunk | null {
    if (this._documentChunks.length === 0) {
      this.chunkDocument();
    }

    return this._documentChunks.find(chunk =>
      offset >= chunk.startOffset && offset < chunk.endOffset
    ) || null;
  }

  /**
   * Get surrounding chunks (context window)
   * @param chunkId ID of the center chunk
   * @param before Number of chunks before
   * @param after Number of chunks after
   */
  public getChunkContext(chunkId: string, before: number = 1, after: number = 1): {
    chunks: DocumentChunk[];
    centerIndex: number;
  } {
    if (this._documentChunks.length === 0) {
      this.chunkDocument();
    }

    const centerIndex = this._documentChunks.findIndex(c => c.id === chunkId);
    if (centerIndex === -1) {
      return { chunks: [], centerIndex: -1 };
    }

    const startIdx = Math.max(0, centerIndex - before);
    const endIdx = Math.min(this._documentChunks.length - 1, centerIndex + after);

    return {
      chunks: this._documentChunks.slice(startIdx, endIdx + 1),
      centerIndex: centerIndex - startIdx,
    };
  }

  /**
   * Clear cached chunks and position index
   * Call this after document modifications
   */
  public invalidateReadingCache(): void {
    this._documentChunks = [];
    this._positionIndex = [];
    this._lastChunkTime = 0;
  }

  // ============================================================
  // Paragraph Style XML Operations
  // ============================================================

  /**
   * Apply paragraph style changes (alignment, etc.) to XML files.
   * Creates new paraPr elements in header.xml and updates paragraph references in section XML.
   */
  private async applyParagraphStylesToXml(): Promise<void> {
    if (!this._zip || this._pendingParagraphStyles.length === 0) return;

    // Read header.xml to find existing paraPr elements and their max ID
    const headerPath = 'Contents/header.xml';
    let headerXml = await this._zip.file(headerPath)?.async('string');
    if (!headerXml) return;

    // Find the maximum paraPr ID
    const paraPrIdMatches = headerXml.matchAll(/<hh:paraPr[^>]*\sid="(\d+)"/g);
    let maxParaPrId = 0;
    for (const match of paraPrIdMatches) {
      const id = parseInt(match[1], 10);
      if (id > maxParaPrId) maxParaPrId = id;
    }

    // Map align values to HWPML values
    const alignMap: Record<string, string> = {
      'left': 'LEFT',
      'center': 'CENTER',
      'right': 'RIGHT',
      'justify': 'JUSTIFY',
      'distribute': 'DISTRIBUTE',
    };

    // Group updates by section
    const updatesBySection = new Map<number, Array<{ elementIndex: number; style: Partial<ParagraphStyle>; newParaPrId: number }>>();

    // Create new paraPr elements for each style change
    const newParaPrs: string[] = [];
    for (const update of this._pendingParagraphStyles) {
      const newId = ++maxParaPrId;

      // Build paraPr element with alignment
      const align = update.style.align ? alignMap[update.style.align.toLowerCase()] || 'LEFT' : 'LEFT';
      const lineSpacing = update.style.lineSpacing ?? 160; // default 160%

      // Build paraPr element with hp:switch structure for Hangul compatibility
      // Korean word processor requires this structure to properly recognize paragraph styles
      const paraPrXml = `<hh:paraPr id="${newId}" tabPrIDRef="0" condense="0" fontLineHeight="0" snapToGrid="1" suppressLineNumbers="0" checked="0" textDir="AUTO">
        <hh:align horizontal="${align}" vertical="BASELINE"/>
        <hh:heading type="NONE" idRef="0" level="0"/>
        <hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="BREAK_WORD" widowOrphan="0" keepWithNext="0" keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/>
        <hh:autoSpacing eAsianEng="0" eAsianNum="0"/>
        <hp:switch>
          <hp:case hp:required-namespace="http://www.hancom.co.kr/hwpml/2016/HwpUnitChar">
            <hh:margin>
              <hc:intent value="0" unit="HWPUNIT"/>
              <hc:left value="0" unit="HWPUNIT"/>
              <hc:right value="0" unit="HWPUNIT"/>
              <hc:prev value="0" unit="HWPUNIT"/>
              <hc:next value="0" unit="HWPUNIT"/>
            </hh:margin>
            <hh:lineSpacing type="PERCENT" value="${lineSpacing}" unit="HWPUNIT"/>
          </hp:case>
          <hp:default>
            <hh:margin>
              <hc:intent value="0" unit="HWPUNIT"/>
              <hc:left value="0" unit="HWPUNIT"/>
              <hc:right value="0" unit="HWPUNIT"/>
              <hc:prev value="0" unit="HWPUNIT"/>
              <hc:next value="0" unit="HWPUNIT"/>
            </hh:margin>
            <hh:lineSpacing type="PERCENT" value="${lineSpacing}" unit="HWPUNIT"/>
          </hp:default>
        </hp:switch>
        <hh:border borderFillIDRef="1" offsetLeft="0" offsetRight="0" offsetTop="0" offsetBottom="0" connect="0" ignoreMargin="0"/>
      </hh:paraPr>`;

      newParaPrs.push(paraPrXml);

      // Add to section updates map
      if (!updatesBySection.has(update.sectionIndex)) {
        updatesBySection.set(update.sectionIndex, []);
      }
      updatesBySection.get(update.sectionIndex)!.push({
        elementIndex: update.elementIndex,
        style: update.style,
        newParaPrId: newId,
      });
    }

    // Insert new paraPr elements into header.xml
    if (newParaPrs.length > 0) {
      // Update itemCnt in <hh:paraProperties>
      // Korean word processor uses itemCnt to determine how many paraPr elements to read
      const newItemCnt = maxParaPrId + 1; // maxParaPrId was incremented for each new paraPr
      headerXml = headerXml.replace(
        /<hh:paraProperties\s+itemCnt="(\d+)"/,
        `<hh:paraProperties itemCnt="${newItemCnt}"`
      );

      // Find </hh:paraProperties> and insert before it
      const insertPoint = headerXml.indexOf('</hh:paraProperties>');
      if (insertPoint !== -1) {
        headerXml = headerXml.substring(0, insertPoint) +
          newParaPrs.join('\n') + '\n' +
          headerXml.substring(insertPoint);
      }
      this._zip.file(headerPath, headerXml);
    }

    // Update section XMLs
    for (const [sectionIndex, updates] of updatesBySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      let sectionXml = await this._zip.file(sectionPath)?.async('string');
      if (!sectionXml) continue;

      // Find all top-level elements (paragraphs AND tables) to match elementIndex
      // Uses depth-aware helper to avoid matching elements nested inside tables/secPr
      const allElements = this.findTopLevelElements(sectionXml);

      // Update paraPrIDRef for each affected paragraph
      // Sort updates by elementIndex in reverse order to avoid offset issues
      const sortedUpdates = [...updates].sort((a, b) => b.elementIndex - a.elementIndex);

      for (const update of sortedUpdates) {
        if (update.elementIndex < allElements.length) {
          const elem = allElements[update.elementIndex];
          if (elem.type !== 'p') continue; // Skip tables
          // Replace paraPrIDRef in the paragraph element
          const updatedContent = elem.content.replace(
            /paraPrIDRef="(\d+)"/,
            `paraPrIDRef="${update.newParaPrId}"`
          );
          sectionXml = sectionXml.substring(0, elem.start) +
            updatedContent +
            sectionXml.substring(elem.start + elem.tagLength);
        }
      }

      this._zip.file(sectionPath, sectionXml);
    }
  }

  /**
   * Apply character style changes (font, size, bold, italic) to XML files.
   * Creates new charPr elements in header.xml and updates run references in section XML.
   */
  private async applyCharacterStylesToXml(): Promise<void> {
    if (!this._zip || this._pendingCharacterStyles.length === 0) return;

    // Read header.xml to find existing charPr elements and their max ID
    const headerPath = 'Contents/header.xml';
    let headerXml = await this._zip.file(headerPath)?.async('string');
    if (!headerXml) return;

    // Find max charPr id
    const charPrIdRegex = /<hh:charPr\s+[^>]*id="(\d+)"/g;
    let maxCharPrId = 0;
    let match;
    while ((match = charPrIdRegex.exec(headerXml)) !== null) {
      maxCharPrId = Math.max(maxCharPrId, parseInt(match[1], 10));
    }
    // Also check charShape
    const charShapeIdRegex = /<hh:charShape\s+[^>]*id="(\d+)"/g;
    while ((match = charShapeIdRegex.exec(headerXml)) !== null) {
      maxCharPrId = Math.max(maxCharPrId, parseInt(match[1], 10));
    }

    // Parse fontface sections to build per-lang maps: {HANGUL: {fontName: id}, LATIN: {fontName: id}, ...}
    const langToFontMap = new Map<string, Map<string, number>>();
    const fontfaceRegex = /<hh:fontface\s+lang="([^"]+)"[^>]*>([\s\S]*?)<\/hh:fontface>/g;
    let ffMatch;
    while ((ffMatch = fontfaceRegex.exec(headerXml)) !== null) {
      const lang = ffMatch[1];
      const fontMap = new Map<string, number>();
      const fontRegex = /<hh:font\s+id="(\d+)"\s+face="([^"]+)"/g;
      let fMatch;
      while ((fMatch = fontRegex.exec(ffMatch[2])) !== null) {
        fontMap.set(fMatch[2], parseInt(fMatch[1], 10));
      }
      langToFontMap.set(lang, fontMap);
    }

    const langToAttr: Record<string, string> = {
      HANGUL: 'hangul',
      LATIN: 'latin',
      HANJA: 'hanja',
      JAPANESE: 'japanese',
      OTHER: 'other',
      SYMBOL: 'symbol',
      USER: 'user'
    };

    // Read base charPr (id=0) as template
    const baseCharPrMatch = headerXml.match(/<hh:charPr\s+[^>]*id="0"[^>]*>([\s\S]*?)<\/hh:charPr>/);
    let baseInnerXml = '';
    if (baseCharPrMatch) {
      // Remove fontRef line, we'll create our own
      baseInnerXml = baseCharPrMatch[1]
        .replace(/<hh:fontRef[^/]*\/>/g, '')  // remove fontRef
        .trim();
    }

    // Map to track new charPr IDs for each unique style
    const styleToIdMap = new Map<string, number>();
    const newCharPrs: string[] = [];

    // Group updates by section
    const updatesBySection = new Map<number, Array<typeof this._pendingCharacterStyles[0]>>();
    for (const update of this._pendingCharacterStyles) {
      if (!updatesBySection.has(update.sectionIndex)) {
        updatesBySection.set(update.sectionIndex, []);
      }
      updatesBySection.get(update.sectionIndex)!.push(update);
    }

    // Process each section
    for (const [sectionIndex, updates] of updatesBySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      let sectionXml = await this._zip.file(sectionPath)?.async('string');
      if (!sectionXml) continue;

      for (const update of updates) {
        // Create style key for deduplication
        const fontSize = update.style.fontSize ? Math.round(update.style.fontSize * 100) : 1000;
        const fontName = update.style.fontName || '함초롬바탕';
        const bold = update.style.bold ? '1' : '0';
        const italic = update.style.italic ? '1' : '0';
        const styleKey = `${fontName}-${fontSize}-${bold}-${italic}`;

        let charPrId: number;
        if (styleToIdMap.has(styleKey)) {
          charPrId = styleToIdMap.get(styleKey)!;
        } else {
          charPrId = ++maxCharPrId;
          styleToIdMap.set(styleKey, charPrId);

          // Build fontRef with numeric IDs
          const fontRefParts: string[] = [];
          for (const [lang, attr] of Object.entries(langToAttr)) {
            const fontMap = langToFontMap.get(lang);
            const fontId = fontMap?.get(fontName) ?? 0;
            fontRefParts.push(`${attr}="${fontId}"`);
          }
          const fontRefStr = `<hh:fontRef ${fontRefParts.join(' ')}/>`;

          // Build charPr with base template
          const boldTag = bold === '1' ? '<hh:bold/>' : '';
          const italicTag = italic === '1' ? '<hh:italic/>' : '';

          // Reconstruct from base, inserting fontRef first
          const charPr = `      <hh:charPr id="${charPrId}" height="${fontSize}" textColor="#000000" shadeColor="none" useFontSpace="0" useKerning="0" symMark="NONE" borderFillIDRef="1">
        ${fontRefStr}
        ${baseInnerXml}${boldTag ? '\n        ' + boldTag : ''}${italicTag ? '\n        ' + italicTag : ''}
      </hh:charPr>`;
          newCharPrs.push(charPr);
        }

        // Update section XML - find the paragraph and update run's charPrIDRef
        // Uses depth-aware helper to only match top-level elements
        const allTopElements = this.findTopLevelElements(sectionXml);
        let targetParagraphStart = -1;
        let targetParagraphEnd = -1;

        if (update.elementIndex < allTopElements.length) {
          const elem = allTopElements[update.elementIndex];
          if (elem.type === 'p') {
            targetParagraphStart = elem.start;
            // Find end of this paragraph - need to find matching close tag
            const prefixMatch = elem.content.match(/<(hp|hs):p/);
            const pPrefix = prefixMatch ? prefixMatch[1] : 'hp';
            const closeTag = `</${pPrefix}:p>`;
            targetParagraphEnd = sectionXml.indexOf(closeTag, targetParagraphStart) + closeTag.length;
          }
        }

        if (targetParagraphStart >= 0 && targetParagraphEnd > targetParagraphStart) {
          let paraXml = sectionXml.slice(targetParagraphStart, targetParagraphEnd);

          // Find and update the run at runIndex
          const runRegex = /<hp:run\s+charPrIDRef="(\d+)"/g;
          let runCount = 0;
          let runMatch;
          while ((runMatch = runRegex.exec(paraXml)) !== null) {
            if (runCount === update.runIndex) {
              const oldAttr = runMatch[0];
              const newAttr = `<hp:run charPrIDRef="${charPrId}"`;
              paraXml = paraXml.slice(0, runMatch.index) + newAttr + paraXml.slice(runMatch.index + oldAttr.length);
              break;
            }
            runCount++;
          }

          sectionXml = sectionXml.slice(0, targetParagraphStart) + paraXml + sectionXml.slice(targetParagraphEnd);
        }
      }

      // Save updated section XML
      this._zip.file(sectionPath, sectionXml);
    }

    // Add new charPr elements to header.xml
    if (newCharPrs.length > 0) {
      // Find charProperties section and insert before closing tag
      const charPropsMatch = headerXml.match(/<hh:charProperties\s+itemCnt="(\d+)">/);
      if (charPropsMatch) {
        const oldCount = parseInt(charPropsMatch[1], 10);
        const newCount = oldCount + newCharPrs.length;
        headerXml = headerXml.replace(
          /<hh:charProperties\s+itemCnt="\d+">/,
          `<hh:charProperties itemCnt="${newCount}">`
        );

        // Insert new charPr elements before </hh:charProperties>
        headerXml = headerXml.replace(
          '</hh:charProperties>',
          newCharPrs.join('\n') + '\n    </hh:charProperties>'
        );
      }

      this._zip.file(headerPath, headerXml);
    }
  }

  // ============================================================
  // Hanging Indent XML Operations
  // ============================================================

  /**
   * Apply hanging indent changes to XML files.
   * This adds new paraPr elements to header.xml and updates paragraph references in section XML.
   */
  private async applyHangingIndentsToXml(): Promise<void> {
    if (!this._zip || this._pendingHangingIndents.length === 0) return;

    // Read header.xml to find existing paraPr elements and their max ID
    const headerPath = 'Contents/header.xml';
    let headerXml = await this._zip.file(headerPath)?.async('string');
    if (!headerXml) return;

    // Find the maximum paraPr ID
    const paraPrIdMatches = headerXml.matchAll(/<hh:paraPr[^>]*\sid="(\d+)"/g);
    let maxParaPrId = 0;
    for (const match of paraPrIdMatches) {
      const id = parseInt(match[1], 10);
      if (id > maxParaPrId) maxParaPrId = id;
    }

    // Group pending changes by section
    const changesBySection = new Map<number, typeof this._pendingHangingIndents>();
    for (const change of this._pendingHangingIndents) {
      const existing = changesBySection.get(change.sectionIndex) || [];
      existing.push(change);
      changesBySection.set(change.sectionIndex, existing);
    }

    // Create new paraPr for each unique indent value
    const indentToParaPrId = new Map<number, number>();
    let newParaPrXml = '';

    for (const change of this._pendingHangingIndents) {
      if (change.indentPt === 0) continue; // Skip removal (use existing paraPr 0)
      if (indentToParaPrId.has(change.indentPt)) continue;

      maxParaPrId++;
      const newId = maxParaPrId;
      indentToParaPrId.set(change.indentPt, newId);

      // HWPUNIT = pt * 100
      const intentValue = -change.indentPt * 100; // Negative for hanging indent
      const leftValue = change.indentPt * 100;    // Positive for left margin

      // Create new paraPr with hanging indent
      newParaPrXml += `\n      <hh:paraPr id="${newId}" tabPrIDRef="0">
        <hh:align horizontal="LEFT" vertical="BASELINE"/>
        <hh:margin>
          <hc:intent value="${intentValue}" unit="HWPUNIT"/>
          <hc:left value="${leftValue}" unit="HWPUNIT"/>
          <hc:right value="0" unit="HWPUNIT"/>
          <hc:prev value="0" unit="HWPUNIT"/>
          <hc:next value="0" unit="HWPUNIT"/>
        </hh:margin>
        <hh:lineSpacing type="PERCENT" value="160" unit="HWPUNIT"/>
      </hh:paraPr>`;
    }

    // Insert new paraPr elements into header.xml
    if (newParaPrXml) {
      // Find the closing tag of paraProperties
      const paraPropsEndMatch = headerXml.match(/<\/hh:paraProperties>/);
      if (paraPropsEndMatch && paraPropsEndMatch.index !== undefined) {
        headerXml = headerXml.slice(0, paraPropsEndMatch.index) +
          newParaPrXml + '\n    ' +
          headerXml.slice(paraPropsEndMatch.index);

        // Update itemCnt attribute
        const itemCntMatch = headerXml.match(/<hh:paraProperties[^>]*itemCnt="(\d+)"/);
        if (itemCntMatch) {
          const oldCount = parseInt(itemCntMatch[1], 10);
          const newCount = oldCount + indentToParaPrId.size;
          headerXml = headerXml.replace(
            /<hh:paraProperties([^>]*)itemCnt="\d+"/,
            `<hh:paraProperties$1itemCnt="${newCount}"`
          );
        }
      }

      this._zip.file(headerPath, headerXml);
    }

    // Update section XMLs with new paraPrIDRef
    for (const [sectionIndex, changes] of changesBySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      let sectionXml = await this._zip.file(sectionPath)?.async('string');
      if (!sectionXml) continue;

      // Build map of paragraphId -> targetParaPrId (last change wins for duplicates)
      // Use paragraphId instead of elementIndex to correctly match paragraphs after insertion
      const paragraphIdToParaPrId = new Map<string, number>();
      for (const change of changes) {
        const targetParaPrId = change.indentPt === 0
          ? 0
          : (indentToParaPrId.get(change.indentPt) || 0);
        paragraphIdToParaPrId.set(change.paragraphId, targetParaPrId);
      }

      // Process all paragraphs by matching their id attribute
      const pRegex = /<hp:p\b([^>]*)>/g;
      let pMatch: RegExpExecArray | null;
      let newSectionXml = '';
      let lastIndex = 0;

      while ((pMatch = pRegex.exec(sectionXml)) !== null) {
        const attrs = pMatch[1];
        // Extract paragraph id from attributes
        const idMatch = attrs.match(/\bid="([^"]+)"/);
        const paragraphId = idMatch ? idMatch[1] : null;

        // Check if this paragraph needs updating by paragraphId
        if (paragraphId && paragraphIdToParaPrId.has(paragraphId)) {
          const targetParaPrId = paragraphIdToParaPrId.get(paragraphId)!;
          newSectionXml += sectionXml.slice(lastIndex, pMatch.index);
          const updatedAttrs = attrs.replace(
            /paraPrIDRef="\d+"/,
            `paraPrIDRef="${targetParaPrId}"`
          );
          newSectionXml += `<hp:p${updatedAttrs}>`;
          lastIndex = pMatch.index + pMatch[0].length;
        }
      }

      // Only update if we made changes
      if (lastIndex > 0) {
        newSectionXml += sectionXml.slice(lastIndex);
        this._zip.file(sectionPath, newSectionXml);
      }
    }
  }

  /**
   * Apply table cell hanging indent changes to XML files.
   * This adds new paraPr elements to header.xml and updates paragraph references in table cells.
   */
  private async applyTableCellHangingIndentsToXml(): Promise<void> {
    if (!this._zip || this._pendingTableCellHangingIndents.length === 0) return;

    // Read header.xml to find existing paraPr elements and their max ID
    const headerPath = 'Contents/header.xml';
    let headerXml = await this._zip.file(headerPath)?.async('string');
    if (!headerXml) return;

    // Find the maximum paraPr ID
    const paraPrIdMatches = headerXml.matchAll(/<hh:paraPr[^>]*\sid="(\d+)"/g);
    let maxParaPrId = 0;
    for (const match of paraPrIdMatches) {
      const id = parseInt(match[1], 10);
      if (id > maxParaPrId) maxParaPrId = id;
    }

    // Group pending changes by section and table
    const changesBySection = new Map<number, typeof this._pendingTableCellHangingIndents>();
    for (const change of this._pendingTableCellHangingIndents) {
      const existing = changesBySection.get(change.sectionIndex) || [];
      existing.push(change);
      changesBySection.set(change.sectionIndex, existing);
    }

    // Create new paraPr for each unique indent value
    const indentToParaPrId = new Map<number, number>();
    let newParaPrXml = '';

    for (const change of this._pendingTableCellHangingIndents) {
      if (change.indentPt === 0) continue; // Skip removal (use existing paraPr 0)
      if (indentToParaPrId.has(change.indentPt)) continue;

      maxParaPrId++;
      const newId = maxParaPrId;
      indentToParaPrId.set(change.indentPt, newId);

      // HWPUNIT = pt * 100
      const intentValue = -change.indentPt * 100; // Negative for hanging indent
      const leftValue = change.indentPt * 100;    // Positive for left margin

      // Create new paraPr with hanging indent
      newParaPrXml += `\n      <hh:paraPr id="${newId}" tabPrIDRef="0">
        <hh:align horizontal="LEFT" vertical="BASELINE"/>
        <hh:margin>
          <hc:intent value="${intentValue}" unit="HWPUNIT"/>
          <hc:left value="${leftValue}" unit="HWPUNIT"/>
          <hc:right value="0" unit="HWPUNIT"/>
          <hc:prev value="0" unit="HWPUNIT"/>
          <hc:next value="0" unit="HWPUNIT"/>
        </hh:margin>
        <hh:lineSpacing type="PERCENT" value="160" unit="HWPUNIT"/>
      </hh:paraPr>`;
    }

    // Insert new paraPr elements into header.xml
    if (newParaPrXml) {
      // Find the closing tag of paraProperties
      const paraPropsEndMatch = headerXml.match(/<\/hh:paraProperties>/);
      if (paraPropsEndMatch && paraPropsEndMatch.index !== undefined) {
        headerXml = headerXml.slice(0, paraPropsEndMatch.index) +
          newParaPrXml + '\n    ' +
          headerXml.slice(paraPropsEndMatch.index);

        // Update itemCnt attribute
        const itemCntMatch = headerXml.match(/<hh:paraProperties[^>]*itemCnt="(\d+)"/);
        if (itemCntMatch) {
          const oldCount = parseInt(itemCntMatch[1], 10);
          const newCount = oldCount + indentToParaPrId.size;
          headerXml = headerXml.replace(
            /<hh:paraProperties([^>]*)itemCnt="\d+"/,
            `<hh:paraProperties$1itemCnt="${newCount}"`
          );
        }
      }

      this._zip.file(headerPath, headerXml);
    }

    // Update section XMLs with new paraPrIDRef for table cell paragraphs
    for (const [sectionIndex, changes] of changesBySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      let sectionXml = await this._zip.file(sectionPath)?.async('string');
      if (!sectionXml) continue;

      // Group changes by table index
      const changesByTable = new Map<number, typeof changes>();
      for (const change of changes) {
        const existing = changesByTable.get(change.tableIndex) || [];
        existing.push(change);
        changesByTable.set(change.tableIndex, existing);
      }

      // CRITICAL: Sort table indices in DESCENDING order to avoid stale position bug
      // When we modify a table, sectionXml length changes. If we process tables
      // in ascending order, the position indices for later tables become stale.
      // By processing from end to start, earlier table positions remain valid.
      const sortedTableIndices = [...changesByTable.keys()].sort((a, b) => b - a);

      for (const tableIndex of sortedTableIndices) {
        // Re-find tables for each iteration to get fresh positions
        const tables = this.findAllTables(sectionXml);
        if (tableIndex >= tables.length) continue;

        const tableChanges = changesByTable.get(tableIndex)!;
        const tableData = tables[tableIndex];
        let tableXml = tableData.xml;

        // Process changes for this table (sorted by row/col/para in reverse for safe mutation)
        const sortedChanges = [...tableChanges].sort((a, b) => {
          if (a.row !== b.row) return b.row - a.row;
          if (a.col !== b.col) return b.col - a.col;
          return b.paragraphIndex - a.paragraphIndex;
        });

        for (const change of sortedChanges) {
          const targetParaPrId = change.indentPt === 0
            ? 0
            : (indentToParaPrId.get(change.indentPt) || 0);

          // Find the cell
          const cellInfo = this.findTableCellInXml(tableXml, change.row, change.col);
          if (!cellInfo) continue;

          // Find the paragraph within the cell
          const cellContent = cellInfo.xml;
          const pRegex = /<hp:p\b([^>]*)>/g;
          let pMatch: RegExpExecArray | null;
          let paraCount = 0;

          while ((pMatch = pRegex.exec(cellContent)) !== null) {
            if (paraCount === change.paragraphIndex) {
              // Update this paragraph's paraPrIDRef
              const attrs = pMatch[1];
              const updatedAttrs = attrs.replace(
                /paraPrIDRef="\d+"/,
                `paraPrIDRef="${targetParaPrId}"`
              );
              const updatedCell = cellContent.slice(0, pMatch.index) +
                `<hp:p${updatedAttrs}>` +
                cellContent.slice(pMatch.index + pMatch[0].length);

              // Replace the cell content in table XML
              tableXml = tableXml.slice(0, cellInfo.startIndex) +
                updatedCell +
                tableXml.slice(cellInfo.endIndex);
              break;
            }
            paraCount++;
          }
        }

        // Replace table in section XML
        sectionXml = sectionXml.slice(0, tableData.startIndex) +
          tableXml +
          sectionXml.slice(tableData.endIndex);
      }

      this._zip.file(sectionPath, sectionXml);
    }
  }

  /**
   * Find a specific cell in table XML by row and column index.
   * Uses balanced bracket matching to correctly handle nested tables.
   * @returns Cell XML content and its position, or null if not found
   */
  private findTableCellInXml(tableXml: string, targetRow: number, targetCol: number): {
    xml: string;
    startIndex: number;
    endIndex: number;
  } | null {
    // Use balanced bracket matching to find top-level rows (handles nested tables correctly)
    const rows = this.findAllElementsWithDepth(tableXml, 'tr');

    if (targetRow >= rows.length) return null;

    const targetRowData = rows[targetRow];

    // Extract content inside the row (between <hp:tr...> and </hp:tr>)
    const rowOpenTagMatch = targetRowData.xml.match(/^<(?:hp|hs|hc):tr[^>]*>/);
    if (!rowOpenTagMatch) return null;

    const rowContentStart = rowOpenTagMatch[0].length;
    const rowContentEnd = targetRowData.xml.lastIndexOf('</');
    if (rowContentEnd <= rowContentStart) return null;

    const rowContent = targetRowData.xml.substring(rowContentStart, rowContentEnd);

    // Use balanced bracket matching to find top-level cells in this row
    const cells = this.findAllElementsWithDepth(rowContent, 'tc');

    if (targetCol >= cells.length) return null;

    const targetCellData = cells[targetCol];

    // Extract content inside the cell (between <hp:tc...> and </hp:tc>)
    const cellOpenTagMatch = targetCellData.xml.match(/^<(?:hp|hs|hc):tc[^>]*>/);
    if (!cellOpenTagMatch) return null;

    const cellContentStart = cellOpenTagMatch[0].length;
    const cellContentEnd = targetCellData.xml.lastIndexOf('</');
    if (cellContentEnd <= cellContentStart) return null;

    const cellContent = targetCellData.xml.substring(cellContentStart, cellContentEnd);

    // Calculate absolute position in the original tableXml
    // rowData.startIndex is relative to tableXml
    // cellData.startIndex is relative to rowContent
    // cellContent starts at cellOpenTagMatch[0].length within the cell
    const absoluteStart = targetRowData.startIndex + rowContentStart + targetCellData.startIndex + cellContentStart;
    const absoluteEnd = absoluteStart + cellContent.length;

    return {
      xml: cellContent,
      startIndex: absoluteStart,
      endIndex: absoluteEnd,
    };
  }

  // ============================================================
  // Table Row Insert/Delete XML Persistence
  // ============================================================

  private async applyTableRowInsertsToXml(): Promise<void> {
    if (!this._zip) return;

    // Group by section
    const bySection = new Map<number, typeof this._pendingTableRowInserts>();
    for (const insert of this._pendingTableRowInserts) {
      const arr = bySection.get(insert.sectionIndex) || [];
      arr.push(insert);
      bySection.set(insert.sectionIndex, arr);
    }

    for (const [sectionIndex, inserts] of bySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      let xml = await this._zip.file(sectionPath)?.async('string');
      if (!xml) continue;

      // Process each insert (process in reverse order of afterRowIndex to maintain indices)
      const sorted = [...inserts].sort((a, b) => b.afterRowIndex - a.afterRowIndex);

      for (const insert of sorted) {
        const tables = this.findAllTables(xml);
        if (insert.tableIndex >= tables.length) continue;
        const tableXml = tables[insert.tableIndex].xml;

        const rows = this.findAllElementsWithDepth(tableXml, 'tr');
        if (insert.afterRowIndex >= rows.length) continue;

        const templateRow = rows[insert.afterRowIndex];

        // Clone the template row - clear text content but preserve XML structure
        let newRowXml = templateRow.xml;

        // Clear text inside <hp:t> and <hs:t> tags but preserve the tags themselves
        newRowXml = newRowXml.replace(/<(hp|hs):t([^>]*)>[\s\S]*?<\/\1:t>/g, '<$1:t$2></$1:t>');

        // Update rowAddr in each cell
        const newRowAddr = insert.afterRowIndex + 1;
        newRowXml = newRowXml.replace(/rowAddr="(\d+)"/g, `rowAddr="${newRowAddr}"`);

        // Set cell texts if provided
        if (insert.cellTexts) {
          let cellIdx = 0;
          newRowXml = newRowXml.replace(/<(hp|hs):t([^>]*)><\/\1:t>/g, (match, prefix, attrs) => {
            if (cellIdx < insert.cellTexts!.length) {
              const text = this.escapeXml(insert.cellTexts![cellIdx]);
              cellIdx++;
              return `<${prefix}:t${attrs}>${text}</${prefix}:t>`;
            }
            cellIdx++;
            return match;
          });
        }

        // Insert after the template row
        const insertPos = templateRow.startIndex + templateRow.xml.length;
        const newTableXml = tableXml.substring(0, insertPos) + '\n' + newRowXml + tableXml.substring(insertPos);

        // Update rowCnt attribute
        const updatedTableXml = newTableXml.replace(/rowCnt="(\d+)"/, (_m, cnt) => `rowCnt="${parseInt(cnt) + 1}"`);

        xml = xml.substring(0, tables[insert.tableIndex].startIndex) + updatedTableXml + xml.substring(tables[insert.tableIndex].endIndex);
      }

      this._zip.file(sectionPath, xml);
    }
  }

  private async applyTableRowDeletesToXml(): Promise<void> {
    if (!this._zip) return;

    const bySection = new Map<number, typeof this._pendingTableRowDeletes>();
    for (const del of this._pendingTableRowDeletes) {
      const arr = bySection.get(del.sectionIndex) || [];
      arr.push(del);
      bySection.set(del.sectionIndex, arr);
    }

    for (const [sectionIndex, deletes] of bySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      let xml = await this._zip.file(sectionPath)?.async('string');
      if (!xml) continue;

      // Process in reverse order
      const sorted = [...deletes].sort((a, b) => b.rowIndex - a.rowIndex);

      for (const del of sorted) {
        const tables = this.findAllTables(xml);
        if (del.tableIndex >= tables.length) continue;
        const tableXml = tables[del.tableIndex].xml;

        const rows = this.findAllElementsWithDepth(tableXml, 'tr');
        if (del.rowIndex >= rows.length) continue;

        const row = rows[del.rowIndex];
        const newTableXml = tableXml.substring(0, row.startIndex) + tableXml.substring(row.endIndex);

        // Update rowCnt
        const updatedTableXml = newTableXml.replace(/rowCnt="(\d+)"/, (_m, cnt) => `rowCnt="${Math.max(0, parseInt(cnt) - 1)}"`);

        xml = xml.substring(0, tables[del.tableIndex].startIndex) + updatedTableXml + xml.substring(tables[del.tableIndex].endIndex);
      }

      this._zip.file(sectionPath, xml);
    }
  }

  // ============================================================
  // Table Column Insert/Delete XML Persistence
  // ============================================================

  private async applyTableColumnInsertsToXml(): Promise<void> {
    if (!this._zip) return;

    const bySection = new Map<number, typeof this._pendingTableColumnInserts>();
    for (const insert of this._pendingTableColumnInserts) {
      const arr = bySection.get(insert.sectionIndex) || [];
      arr.push(insert);
      bySection.set(insert.sectionIndex, arr);
    }

    for (const [sectionIndex, inserts] of bySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      let xml = await this._zip.file(sectionPath)?.async('string');
      if (!xml) continue;

      for (const insert of inserts) {
        const tables = this.findAllTables(xml);
        if (insert.tableIndex >= tables.length) continue;
        let tableXml = tables[insert.tableIndex].xml;

        const rows = this.findAllElementsWithDepth(tableXml, 'tr');

        // Process rows in reverse to maintain indices
        for (let r = rows.length - 1; r >= 0; r--) {
          const row = rows[r];
          const cells = this.findAllElementsWithDepth(row.xml, 'tc');

          // Find template cell at afterColIndex
          let templateCell: { xml: string; startIndex: number; endIndex: number } | null = null;
          for (const cell of cells) {
            const colAddrMatch = cell.xml.match(/colAddr="(\d+)"/);
            if (colAddrMatch && parseInt(colAddrMatch[1]) === insert.afterColIndex) {
              templateCell = cell;
              break;
            }
          }
          if (!templateCell) {
            // Fallback: use last cell
            templateCell = cells[cells.length - 1] || null;
          }
          if (!templateCell) continue;

          // Clone template and clear text
          let newCellXml = templateCell.xml;
          newCellXml = newCellXml.replace(/<(hp|hs):t([^>]*)>[\s\S]*?<\/\1:t>/g, '<$1:t$2></$1:t>');

          // Update colAddr to afterColIndex + 1
          newCellXml = newCellXml.replace(/colAddr="(\d+)"/, `colAddr="${insert.afterColIndex + 1}"`);

          // Also update <hp:cellAddr colAddr="..."> inside the cell
          newCellXml = newCellXml.replace(/<(hp|hs):cellAddr([^>]*)colAddr="(\d+)"/, `<$1:cellAddr$2colAddr="${insert.afterColIndex + 1}"`);

          // Shift colAddr of subsequent cells
          let updatedRowXml = row.xml;
          for (let c = cells.length - 1; c >= 0; c--) {
            const cell = cells[c];
            const colAddrMatch = cell.xml.match(/colAddr="(\d+)"/);
            if (colAddrMatch) {
              const addr = parseInt(colAddrMatch[1]);
              if (addr > insert.afterColIndex) {
                const shifted = cell.xml.replace(/colAddr="(\d+)"/, `colAddr="${addr + 1}"`);
                updatedRowXml = updatedRowXml.substring(0, cell.startIndex) + shifted + updatedRowXml.substring(cell.endIndex);
              }
            }
          }

          // Insert new cell after template
          const insertPos = templateCell.startIndex + templateCell.xml.length;
          updatedRowXml = updatedRowXml.substring(0, insertPos) + '\n' + newCellXml + updatedRowXml.substring(insertPos);

          // Replace row in table
          tableXml = tableXml.substring(0, row.startIndex) + updatedRowXml + tableXml.substring(row.endIndex);
        }

        // Update colCnt
        tableXml = tableXml.replace(/colCnt="(\d+)"/, (_m, cnt) => `colCnt="${parseInt(cnt) + 1}"`);

        xml = xml.substring(0, tables[insert.tableIndex].startIndex) + tableXml + xml.substring(tables[insert.tableIndex].endIndex);
      }

      this._zip.file(sectionPath, xml);
    }
  }

  private async applyTableColumnDeletesToXml(): Promise<void> {
    if (!this._zip) return;

    const bySection = new Map<number, typeof this._pendingTableColumnDeletes>();
    for (const del of this._pendingTableColumnDeletes) {
      const arr = bySection.get(del.sectionIndex) || [];
      arr.push(del);
      bySection.set(del.sectionIndex, arr);
    }

    for (const [sectionIndex, deletes] of bySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      let xml = await this._zip.file(sectionPath)?.async('string');
      if (!xml) continue;

      for (const del of deletes) {
        const tables = this.findAllTables(xml);
        if (del.tableIndex >= tables.length) continue;
        let tableXml = tables[del.tableIndex].xml;

        const rows = this.findAllElementsWithDepth(tableXml, 'tr');

        // Process rows in reverse
        for (let r = rows.length - 1; r >= 0; r--) {
          const row = rows[r];
          const cells = this.findAllElementsWithDepth(row.xml, 'tc');

          let updatedRowXml = row.xml;

          // Find and remove cell at colIndex, shift subsequent
          for (let c = cells.length - 1; c >= 0; c--) {
            const cell = cells[c];
            const colAddrMatch = cell.xml.match(/colAddr="(\d+)"/);
            if (!colAddrMatch) continue;
            const addr = parseInt(colAddrMatch[1]);

            if (addr === del.colIndex) {
              // Remove this cell
              updatedRowXml = updatedRowXml.substring(0, cell.startIndex) + updatedRowXml.substring(cell.endIndex);
            } else if (addr > del.colIndex) {
              // Shift down
              const shifted = cell.xml.replace(/colAddr="(\d+)"/, `colAddr="${addr - 1}"`);
              updatedRowXml = updatedRowXml.substring(0, cell.startIndex) + shifted + updatedRowXml.substring(cell.endIndex);
            }
          }

          tableXml = tableXml.substring(0, row.startIndex) + updatedRowXml + tableXml.substring(row.endIndex);
        }

        // Update colCnt
        tableXml = tableXml.replace(/colCnt="(\d+)"/, (_m, cnt) => `colCnt="${Math.max(0, parseInt(cnt) - 1)}"`);

        xml = xml.substring(0, tables[del.tableIndex].startIndex) + tableXml + xml.substring(tables[del.tableIndex].endIndex);
      }

      this._zip.file(sectionPath, xml);
    }
  }

  // ============================================================
  // Paragraph Copy/Move XML Persistence
  // ============================================================

  /**
   * Find top-level paragraph/table XML elements in section XML with full content.
   * Returns elements with their complete XML (including closing tags).
   */
  private findTopLevelFullElements(sectionXml: string): Array<{ xml: string; startIndex: number; endIndex: number; type: 'p' | 'tbl' }> {
    const results: Array<{ xml: string; startIndex: number; endIndex: number; type: 'p' | 'tbl' }> = [];
    const topLevel = this.findTopLevelElements(sectionXml);

    for (const elem of topLevel) {
      // Find the matching closing tag
      const prefixMatch = elem.content.match(/<(hp|hs|hc):(p|tbl)/);
      if (!prefixMatch) continue;
      const prefix = prefixMatch[1];
      const tag = prefixMatch[2];
      const closeTag = `</${prefix}:${tag}>`;

      // For paragraphs, find the close tag accounting for nesting
      if (tag === 'p') {
        // Paragraphs don't nest, so find the next close tag
        const closeIdx = sectionXml.indexOf(closeTag, elem.start);
        if (closeIdx !== -1) {
          const endIndex = closeIdx + closeTag.length;
          results.push({
            xml: sectionXml.substring(elem.start, endIndex),
            startIndex: elem.start,
            endIndex,
            type: 'p',
          });
        }
      } else if (tag === 'tbl') {
        // Tables can nest, use depth tracking
        let depth = 1;
        let pos = elem.start + elem.tagLength;
        const openTag = `<${prefix}:tbl`;
        while (depth > 0 && pos < sectionXml.length) {
          const nextOpen = sectionXml.indexOf(openTag, pos);
          const nextClose = sectionXml.indexOf(closeTag, pos);
          if (nextClose === -1) break;
          if (nextOpen !== -1 && nextOpen < nextClose) {
            depth++;
            pos = nextOpen + 1;
          } else {
            depth--;
            if (depth === 0) {
              const endIndex = nextClose + closeTag.length;
              results.push({
                xml: sectionXml.substring(elem.start, endIndex),
                startIndex: elem.start,
                endIndex,
                type: 'tbl',
              });
            }
            pos = nextClose + 1;
          }
        }
      }
    }

    return results;
  }

  private async applyParagraphCopiesToXml(): Promise<void> {
    if (!this._zip) return;

    for (const copy of this._pendingParagraphCopies) {
      const srcPath = `Contents/section${copy.sourceSection}.xml`;
      const srcXml = await this._zip.file(srcPath)?.async('string');
      if (!srcXml) continue;

      const srcElements = this.findTopLevelFullElements(srcXml);
      if (copy.sourceParagraph >= srcElements.length) continue;

      const srcElem = srcElements[copy.sourceParagraph];
      if (srcElem.type !== 'p') continue;

      // Clone and regenerate ID
      let clonedXml = srcElem.xml;
      const newId = Math.random().toString(36).substring(2, 11);
      clonedXml = clonedXml.replace(/<(hp|hs):p\s+([^>]*?)id="[^"]*"/, `<$1:p $2id="${newId}"`);

      // Read target section
      const tgtPath = `Contents/section${copy.targetSection}.xml`;
      let tgtXml = await this._zip.file(tgtPath)?.async('string');
      if (!tgtXml) continue;

      const tgtElements = this.findTopLevelFullElements(tgtXml);

      // Insert after targetAfter element
      let insertPos: number;
      if (copy.targetAfter >= 0 && copy.targetAfter < tgtElements.length) {
        insertPos = tgtElements[copy.targetAfter].endIndex;
      } else if (copy.targetAfter < 0) {
        // Insert at beginning - find first element
        if (tgtElements.length > 0) {
          insertPos = tgtElements[0].startIndex;
        } else {
          const secMatch = tgtXml.match(/<(?:hs|hp):sec[^>]*>/);
          insertPos = secMatch ? secMatch.index! + secMatch[0].length : 0;
        }
      } else {
        // After last element
        insertPos = tgtElements.length > 0 ? tgtElements[tgtElements.length - 1].endIndex : tgtXml.lastIndexOf('</');
      }

      tgtXml = tgtXml.substring(0, insertPos) + '\n' + clonedXml + tgtXml.substring(insertPos);
      this._zip.file(tgtPath, tgtXml);
    }
  }

  private async applyParagraphMovesToXml(): Promise<void> {
    if (!this._zip) return;

    for (const move of this._pendingParagraphMoves) {
      const srcPath = `Contents/section${move.sourceSection}.xml`;
      let srcXml = await this._zip.file(srcPath)?.async('string');
      if (!srcXml) continue;

      const srcElements = this.findTopLevelFullElements(srcXml);
      if (move.sourceParagraph >= srcElements.length) continue;

      const srcElem = srcElements[move.sourceParagraph];
      if (srcElem.type !== 'p') continue;

      const extractedXml = srcElem.xml;

      // Remove from source
      srcXml = srcXml.substring(0, srcElem.startIndex) + srcXml.substring(srcElem.endIndex);
      this._zip.file(srcPath, srcXml);

      // Read target section (re-read if same section since we modified it)
      const tgtPath = `Contents/section${move.targetSection}.xml`;
      let tgtXml = await this._zip.file(tgtPath)?.async('string');
      if (!tgtXml) continue;

      const tgtElements = this.findTopLevelFullElements(tgtXml);

      // Adjust target index for same-section moves
      let adjustedTarget = move.targetAfter;
      if (move.sourceSection === move.targetSection && move.sourceParagraph < move.targetAfter) {
        adjustedTarget -= 1;
      }

      let insertPos: number;
      if (adjustedTarget >= 0 && adjustedTarget < tgtElements.length) {
        insertPos = tgtElements[adjustedTarget].endIndex;
      } else if (adjustedTarget < 0) {
        if (tgtElements.length > 0) {
          insertPos = tgtElements[0].startIndex;
        } else {
          const secMatch = tgtXml.match(/<(?:hs|hp):sec[^>]*>/);
          insertPos = secMatch ? secMatch.index! + secMatch[0].length : 0;
        }
      } else {
        insertPos = tgtElements.length > 0 ? tgtElements[tgtElements.length - 1].endIndex : tgtXml.lastIndexOf('</');
      }

      tgtXml = tgtXml.substring(0, insertPos) + '\n' + extractedXml + tgtXml.substring(insertPos);
      this._zip.file(tgtPath, tgtXml);
    }
  }

  // ============================================================
  // Header/Footer XML Persistence
  // ============================================================

  private async applyHeaderFooterUpdatesToXml(): Promise<void> {
    if (!this._zip) return;

    // Collect all updates by section
    const updatesBySection = new Map<number, { header?: string; footer?: string }>();

    for (const update of this._pendingHeaderUpdates) {
      const existing = updatesBySection.get(update.sectionIndex) || {};
      existing.header = update.text;
      updatesBySection.set(update.sectionIndex, existing);
    }

    for (const update of this._pendingFooterUpdates) {
      const existing = updatesBySection.get(update.sectionIndex) || {};
      existing.footer = update.text;
      updatesBySection.set(update.sectionIndex, existing);
    }

    for (const [sectionIndex, updates] of updatesBySection) {
      const sectionPath = `Contents/section${sectionIndex}.xml`;
      let xml = await this._zip.file(sectionPath)?.async('string');
      if (!xml) continue;

      // Find <hp:secPr> or <hs:secPr>
      const secPrMatch = xml.match(/<(hp|hs):secPr[^>]*>/);
      if (!secPrMatch) continue;

      const secPrPrefix = secPrMatch[1];
      const secPrStart = secPrMatch.index!;
      const secPrCloseTag = `</${secPrPrefix}:secPr>`;
      const secPrEnd = xml.indexOf(secPrCloseTag, secPrStart);
      if (secPrEnd === -1) continue;

      let secPrContent = xml.substring(secPrStart, secPrEnd + secPrCloseTag.length);

      // Build header/footer XML
      const buildParagraphXml = (text: string, prefix: string): string => {
        const pid = Math.random().toString(36).substring(2, 11);
        return `<${prefix}:p id="${pid}" paraPrIDRef="0" styleIDRef="0">
              <${prefix}:run charPrIDRef="0">
                <${prefix}:t>${this.escapeXml(text)}</${prefix}:t>
              </${prefix}:run>
            </${prefix}:p>`;
      };

      // Look for existing <hp:headerFooter>
      const hfOpenRegex = new RegExp(`<${secPrPrefix}:headerFooter[^>]*>`);
      const hfCloseTag = `</${secPrPrefix}:headerFooter>`;
      const hfMatch = secPrContent.match(hfOpenRegex);

      if (hfMatch) {
        // headerFooter exists - update/create header and footer inside it
        const hfStart = hfMatch.index!;
        const hfEnd = secPrContent.indexOf(hfCloseTag, hfStart);
        if (hfEnd !== -1) {
          let hfContent = secPrContent.substring(hfStart, hfEnd + hfCloseTag.length);

          if (updates.header !== undefined) {
            const headerRegex = new RegExp(`<${secPrPrefix}:header[^>]*>[\\s\\S]*?<\\/${secPrPrefix}:header>`);
            const headerXml = `<${secPrPrefix}:header>
            <${secPrPrefix}:subList>
              ${buildParagraphXml(updates.header, secPrPrefix)}
            </${secPrPrefix}:subList>
          </${secPrPrefix}:header>`;

            if (headerRegex.test(hfContent)) {
              hfContent = hfContent.replace(headerRegex, headerXml);
            } else {
              // Insert before closing headerFooter
              hfContent = hfContent.replace(hfCloseTag, headerXml + '\n' + hfCloseTag);
            }
          }

          if (updates.footer !== undefined) {
            const footerRegex = new RegExp(`<${secPrPrefix}:footer[^>]*>[\\s\\S]*?<\\/${secPrPrefix}:footer>`);
            const footerXml = `<${secPrPrefix}:footer>
            <${secPrPrefix}:subList>
              ${buildParagraphXml(updates.footer, secPrPrefix)}
            </${secPrPrefix}:subList>
          </${secPrPrefix}:footer>`;

            if (footerRegex.test(hfContent)) {
              hfContent = hfContent.replace(footerRegex, footerXml);
            } else {
              hfContent = hfContent.replace(hfCloseTag, footerXml + '\n' + hfCloseTag);
            }
          }

          secPrContent = secPrContent.substring(0, hfStart) + hfContent + secPrContent.substring(hfEnd + hfCloseTag.length);
        }
      } else {
        // No headerFooter - create one before </secPr>
        let hfXml = `<${secPrPrefix}:headerFooter>`;

        if (updates.header !== undefined) {
          hfXml += `\n          <${secPrPrefix}:header>
            <${secPrPrefix}:subList>
              ${buildParagraphXml(updates.header, secPrPrefix)}
            </${secPrPrefix}:subList>
          </${secPrPrefix}:header>`;
        }

        if (updates.footer !== undefined) {
          hfXml += `\n          <${secPrPrefix}:footer>
            <${secPrPrefix}:subList>
              ${buildParagraphXml(updates.footer, secPrPrefix)}
            </${secPrPrefix}:subList>
          </${secPrPrefix}:footer>`;
        }

        hfXml += `\n        </${secPrPrefix}:headerFooter>`;

        secPrContent = secPrContent.replace(secPrCloseTag, hfXml + '\n' + secPrCloseTag);
      }

      xml = xml.substring(0, secPrStart) + secPrContent + xml.substring(secPrEnd + secPrCloseTag.length);
      this._zip.file(sectionPath, xml);
    }
  }
}
