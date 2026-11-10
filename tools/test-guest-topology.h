/* SPDX-License-Identifier: MIT */
/* Shared pieces of the guest topology host test. tools/test-guest-topology.c owns
 * the group bookkeeping, the placement, identity and PPI groups and the command
 * line; tools/test-guest-topology-gicr.c holds the redistributor discovery groups
 * and the synthetic redistributor memory they run against. */
#ifndef TEST_GUEST_TOPOLOGY_H
#define TEST_GUEST_TOPOLOGY_H
#include <stddef.h>
#include <stdint.h>
#include <string.h>
#include "../compat/topology.h"

extern unsigned checks, failures;

/* Starts or resumes a named group; failures are counted per group. */
void begin(const char *name);
void fail(const char *file, int line, const char *text, int has_values,
          unsigned long long actual, unsigned long long expected);

#define CHECK(cond) do { \
    checks++; \
    if (!(cond)) \
        fail(__FILE__, __LINE__, #cond, 0, 0, 0); \
} while (0)

#define CHECK_EQ(actual, expected) do { \
    unsigned long long a_ = (unsigned long long)(actual); \
    unsigned long long e_ = (unsigned long long)(expected); \
    checks++; \
    if (a_ != e_) \
        fail(__FILE__, __LINE__, #actual " == " #expected, 1, a_, e_); \
} while (0)

/* Machine tables written out by hand, independent of the profiles. core[] lists
 * every physical affinity in cluster order. */
struct machine {
    const struct topology_profile *profile;
    unsigned clusters, cluster_cores, cores, max_vcpus;
    uint64_t core[18];
};

extern struct machine sp11, a16;

static inline void poison(void *p, size_t n)
{
    memset(p, 0xa5, n);
}

/* Whole-string unsigned parse (any base strtoull accepts); 0 on a bad number. */
int parse_number(const char *text, uint64_t *value);

/* The redistributor discovery groups, in the order they must run. */
void test_gicr_groups(void);

/* The `--assign` tail of the command line: builds a synthetic redistributor
 * image from argv[7..11], discovers it and assigns frames to the placement.
 * Returns the process exit status; a malformed argument returns 2. */
int gicr_cli_assign(const struct topology_placement *placement, char **argv);

#endif
