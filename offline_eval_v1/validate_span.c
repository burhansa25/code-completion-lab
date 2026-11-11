/* Standalone, read-only Tree-sitter evaluator for a completed source span.
 * Build against the user's existing modified Tree-sitter core. No core edits.
 * Syntax success is not compilation, execution, or semantic correctness.
 */
#include <tree_sitter/api.h>
#include <ctype.h>
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const TSLanguage *language;
static uint32_t span_start, span_end;
static const char *target;
static bool first_candidate = true, matched = false;
static unsigned error_count = 0, missing_count = 0;

static void json_string(const char *s) {
  putchar('"');
  for (const unsigned char *p=(const unsigned char *)s; *p; p++) {
    if (*p=='"' || *p=='\\') { putchar('\\'); putchar(*p); }
    else if (*p=='\n') fputs("\\n", stdout);
    else if (*p=='\r') fputs("\\r", stdout);
    else if (*p=='\t') fputs("\\t", stdout);
    else if (*p<32) printf("\\u%04x", *p);
    else putchar(*p);
  }
  putchar('"');
}

static bool same_ignoring_space(const char *a, const char *b) {
  for (;;) {
    while (*a && isspace((unsigned char)*a)) a++;
    while (*b && isspace((unsigned char)*b)) b++;
    if (*a!=*b) return false;
    if (!*a) return true;
    a++; b++;
  }
}

static void visit(TSNode parent) {
  if (ts_node_is_error(parent)) error_count++;
  if (ts_node_is_missing(parent)) missing_count++;
  uint32_t count=ts_node_child_count(parent);
  for (uint32_t i=0; i<count; i++) {
    TSNode child=ts_node_child(parent, i);
    /* Match collection's leaf-and-following-siblings form, but require the
     * entire sequence to cover exactly the declared response span. Public
     * node API may omit hidden/repeat nodes: Python calibrates this method
     * against each original reference before interpreting match failures.
     */
    if (!ts_node_is_extra(child) && ts_node_child_count(child)==0 &&
        ts_node_start_byte(child)==span_start &&
        !ts_node_is_missing(child)) {
      uint32_t end=ts_node_end_byte(child);
      size_t length=0;
      for (uint32_t j=i; j<count; j++) {
        TSNode sibling=ts_node_child(parent,j);
        if (ts_node_is_extra(sibling)) continue;
        const char *name=ts_language_symbol_name(language,ts_node_grammar_symbol(sibling));
        length+=strlen(name ? name : "UNKNOWN")+1;
        end=ts_node_end_byte(sibling);
      }
      if (end==span_end) {
        char *sequence=calloc(length+1,1);
        if (!sequence) { fputs("Out of memory\n",stderr); exit(3); }
        bool first=true;
        for (uint32_t j=i; j<count; j++) {
          TSNode sibling=ts_node_child(parent,j);
          if (ts_node_is_extra(sibling)) continue;
          const char *name=ts_language_symbol_name(language,ts_node_grammar_symbol(sibling));
          if (!first) strcat(sequence," ");
          strcat(sequence,name ? name : "UNKNOWN"); first=false;
        }
        if (!first_candidate) putchar(',');
        json_string(sequence); first_candidate=false;
        if (same_ignoring_space(sequence,target)) matched=true;
        free(sequence);
      }
    }
    visit(child);
  }
}

int main(int argc,char **argv) {
  if (argc!=7) {
    fputs("Usage: validate_span grammar.so language_symbol file start_byte end_byte target_structure\n",stderr);
    return 2;
  }
  char *tail;
  unsigned long start=strtoul(argv[4],&tail,10);
  if (*tail || start>UINT32_MAX) return 2;
  unsigned long end=strtoul(argv[5],&tail,10);
  if (*tail || end>UINT32_MAX || end<start) return 2;
  span_start=(uint32_t)start; span_end=(uint32_t)end; target=argv[6];
  void *handle=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);
  if (!handle) { fprintf(stderr,"dlopen: %s\n",dlerror()); return 2; }
  const TSLanguage *(*factory)(void)=NULL;
  *(void **)(&factory)=dlsym(handle,argv[2]);
  if (!factory) { fputs("Language symbol not found\n",stderr); dlclose(handle); return 2; }
  language=factory();
  FILE *file=fopen(argv[3],"rb");
  if (!file) { perror("fopen"); dlclose(handle); return 2; }
  if (fseek(file,0,SEEK_END)!=0) return 2;
  long bytes=ftell(file);
  if (bytes<0 || (unsigned long)bytes>UINT32_MAX || span_end>(unsigned long)bytes) return 2;
  rewind(file);
  char *source=malloc((size_t)bytes+1);
  if (!source) return 3;
  if (fread(source,1,(size_t)bytes,file)!=(size_t)bytes) return 2;
  fclose(file); source[bytes]='\0';
  TSParser *parser=ts_parser_new();
  if (!ts_parser_set_language(parser,language)) {
    fputs("Tree-sitter/grammar ABI mismatch\n",stderr); return 2;
  }
  TSTree *tree=ts_parser_parse_string(parser,NULL,source,(uint32_t)bytes);
  if (!tree) { fputs("Parse failed\n",stderr); return 2; }
  TSNode root=ts_tree_root_node(tree);
  printf("{\"syntax_valid\":%s,\"span_candidates\":[",ts_node_has_error(root)?"false":"true");
  visit(root);
  printf("],\"exact_structure_match\":%s,\"error_nodes\":%u,\"missing_nodes\":%u}\n",
         matched?"true":"false",error_count,missing_count);
  ts_tree_delete(tree);ts_parser_delete(parser);free(source);dlclose(handle);
  return 0;
}
