"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export function MarkdownPreview({ content }: { content: string }) {
  return (
    <div className="prose prose-lg max-w-none 
      prose-headings:text-zinc-900 prose-headings:font-bold prose-headings:border-b prose-headings:border-zinc-100 prose-headings:pb-2 prose-headings:mb-4
      prose-h1:text-2xl prose-h2:text-xl prose-h3:text-lg
      prose-p:text-zinc-700 prose-p:leading-relaxed prose-p:mb-4
      prose-strong:text-zinc-900 prose-strong:font-semibold
      prose-ul:list-disc prose-ul:pl-6 prose-ul:mb-4
      prose-ol:list-decimal prose-ol:pl-6 prose-ol:mb-4
      prose-li:text-zinc-700 prose-li:mb-1
      prose-blockquote:border-l-4 prose-blockquote:border-zinc-300 prose-blockquote:pl-4 prose-blockquote:italic prose-blockquote:text-zinc-600
      prose-code:bg-zinc-100 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-sm
      prose-pre:bg-zinc-100 prose-pre:p-4 prose-pre:rounded-lg
      prose-table:border-collapse prose-th:border prose-th:border-zinc-300 prose-th:px-4 prose-th:py-2 prose-th:bg-zinc-50
      prose-td:border prose-td:border-zinc-300 prose-td:px-4 prose-td:py-2">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>
        {content}
      </ReactMarkdown>
    </div>
  );
}
