<script setup lang="ts">
type InlinePiece = { text: string; code: boolean };
type TranscriptBlock =
  | { kind: "heading"; level: number; content: InlinePiece[] }
  | { kind: "paragraph" | "quote"; content: InlinePiece[] }
  | { kind: "list"; items: InlinePiece[][] }
  | { kind: "table"; head: string[]; rows: string[][] };

defineProps<{ text: string }>();

function inlinePieces(value: string): InlinePiece[] {
  return value.split(/(`[^`]+`)/g).filter(Boolean).map((part) => ({
    text: part.startsWith("`") && part.endsWith("`")
      ? part.slice(1, -1)
      : part.replace(/\*\*(.*?)\*\*/g, "$1").replace(/__(.*?)__/g, "$1"),
    code: part.startsWith("`") && part.endsWith("`"),
  }));
}

function cleanMarkdown(value: string): string {
  return value.replace(/\*\*(.*?)\*\*/g, "$1").replace(/__(.*?)__/g, "$1").replace(/`([^`]+)`/g, "$1");
}

function parseTranscript(text: string): TranscriptBlock[] {
  const blocks: TranscriptBlock[] = [];
  const lines = text.replace(/\r/g, "").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index].trim();
    if (!line) { index += 1; continue; }
    const heading = line.match(/^(#{1,4})\s+(.+)$/);
    if (heading) {
      blocks.push({ kind: "heading", level: heading[1].length, content: inlinePieces(heading[2]) });
      index += 1;
      continue;
    }
    if (line.startsWith("|") && lines[index + 1]?.trim().match(/^\|?[\s:|-]+\|?$/)) {
      const rows = [line.split("|").map((cell) => cleanMarkdown(cell.trim())).filter(Boolean)];
      index += 2;
      while (index < lines.length && lines[index].trim().startsWith("|")) {
        rows.push(lines[index].trim().split("|").map((cell) => cleanMarkdown(cell.trim())).filter(Boolean));
        index += 1;
      }
      blocks.push({ kind: "table", head: rows[0], rows: rows.slice(1) });
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items: InlinePiece[][] = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index].trim())) {
        items.push(inlinePieces(lines[index].trim().replace(/^[-*]\s+/, "")));
        index += 1;
      }
      blocks.push({ kind: "list", items });
      continue;
    }
    if (line.startsWith(">")) {
      const quote: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith(">")) {
        quote.push(lines[index].trim().replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push({ kind: "quote", content: inlinePieces(quote.join("\n")) });
      continue;
    }
    const paragraph = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^(#{1,4})\s+|^[-*]\s+|^>|^\|/.test(lines[index].trim())) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push({ kind: "paragraph", content: inlinePieces(paragraph.join(" ")) });
  }
  return blocks;
}
</script>

<template>
  <div class="transcript">
    <template v-for="(block, blockIndex) in parseTranscript(text)" :key="blockIndex">
      <component v-if="block.kind === 'heading'" :is="block.level <= 2 ? 'h4' : 'h5'">
        <template v-for="(piece, pieceIndex) in block.content" :key="pieceIndex"><span v-if="piece.code" class="inline-code">{{ piece.text }}</span><template v-else>{{ piece.text }}</template></template>
      </component>
      <p v-else-if="block.kind === 'paragraph'">
        <template v-for="(piece, pieceIndex) in block.content" :key="pieceIndex"><span v-if="piece.code" class="inline-code">{{ piece.text }}</span><template v-else>{{ piece.text }}</template></template>
      </p>
      <blockquote v-else-if="block.kind === 'quote'">
        <template v-for="(piece, pieceIndex) in block.content" :key="pieceIndex"><span v-if="piece.code" class="inline-code">{{ piece.text }}</span><template v-else>{{ piece.text }}</template></template>
      </blockquote>
      <ul v-else-if="block.kind === 'list'">
        <li v-for="(item, itemIndex) in block.items" :key="itemIndex"><template v-for="(piece, pieceIndex) in item" :key="pieceIndex"><span v-if="piece.code" class="inline-code">{{ piece.text }}</span><template v-else>{{ piece.text }}</template></template></li>
      </ul>
      <table v-else-if="block.kind === 'table'">
        <thead><tr><th v-for="(cell, cellIndex) in block.head" :key="cellIndex">{{ cell }}</th></tr></thead>
        <tbody><tr v-for="(row, rowIndex) in block.rows" :key="rowIndex"><td v-for="(cell, cellIndex) in row" :key="cellIndex">{{ cell }}</td></tr></tbody>
      </table>
    </template>
  </div>
</template>
