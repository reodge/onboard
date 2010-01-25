/*
This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.

Author: marmuta <marmvta@gmail.com>
*/

#include <stdio.h>
#include <assert.h>
#include <cstring>
#include <set>

//------------------------------------------------------------------------
// NGramTrie - root node of the ngram trie
//------------------------------------------------------------------------

// Look up node or create it if it doesn't exist
template <class TNODE, class TBEFORELASTNODE, class TLASTNODE>
BaseNode* NGramTrie<TNODE, TBEFORELASTNODE, TLASTNODE>::
    add_node(const WordId* wids, int n)
{
    BaseNode* node = this;
    BaseNode* parent = NULL;
    TNODE* grand_parent = NULL;
    int parent_index = 0;
    int grand_parent_index = 0;

    for (int i=0; i<n; i++)
    {
        WordId wid = wids[i];
        grand_parent = static_cast<TNODE*>(parent);
        parent = node;
        grand_parent_index = parent_index;
        node = get_child(parent, i, wid, parent_index);
        if (!node)
        {
            if (i == order-1)
            {
                TBEFORELASTNODE* p = static_cast<TBEFORELASTNODE*>(parent);

                // check the available space for LastNodes
                int size = p->children.size();
                int old_capacity = p->children.capacity();
                if (size >= old_capacity)  // no space for another TLASTNODE?
                {
                    // grow the memory block of the parent node
                    int new_capacity = p->children.capacity(size + 1);
                    int old_bytes = sizeof(TBEFORELASTNODE) +
                               old_capacity*sizeof(TLASTNODE);
                    int new_bytes = sizeof(TBEFORELASTNODE) +
                               new_capacity*sizeof(TLASTNODE);
                    TBEFORELASTNODE* pnew = (TBEFORELASTNODE*) MemAlloc(new_bytes);
                    if (!pnew)
                        return NULL;

                    // copy the data over, no need for constructor calls
                    memcpy(pnew, p, old_bytes);

                    // replace grand_parent pointer
                    ASSERT(p == grand_parent->children[grand_parent_index]);
                    grand_parent->children[grand_parent_index] = pnew;
                    MemFree(p);
                    p = pnew;
                }

                // add the new child node
                node = p->add_child(wid);
            }
            else
            if (i == order-2)
            {
                int bytes = sizeof(TBEFORELASTNODE) +
                        inplace_vector<TLASTNODE>::capacity(0)*sizeof(TLASTNODE);
                TBEFORELASTNODE* nd = (TBEFORELASTNODE*)MemAlloc(bytes);
                //node = new TBEFORELASTNODE(wid);
                if (!nd)
                    return NULL;
                node = new(nd) TBEFORELASTNODE(wid);
                static_cast<TNODE*>(parent)->add_child(node);
            }
            else
            {
                TNODE* nd = (TNODE*)MemAlloc(sizeof(TNODE));
                if (!nd)
                    return NULL;
                node = new(nd) TNODE(wid);
                static_cast<TNODE*>(parent)->add_child(node);
            }

            num_ngrams[i]++; // keep track of the counts to avoid
                             // traversing the tree for these numbers
            break;
        }
    }
    return node;
}

template <class TNODE, class TBEFORELASTNODE, class TLASTNODE>
void NGramTrie<TNODE, TBEFORELASTNODE, TLASTNODE>::
    get_probs_witten_bell_i(const std::vector<WordId>& history,
                             const std::vector<WordId>& words,
                             std::vector<double>& vp,
                             int num_word_types)
{
    int i,j;
    int n = history.size() + 1;
    int size = words.size();   // number of candidate words
    std::vector<int32_t> vc(size);  // vector of counts, reused for order 1..n

    // order 0
    vp.resize(size);
    fill(vp.begin(), vp.end(), 1.0/num_word_types); // uniform distribution

    // order 1..n
    for(j=0; j<n; j++)
    {
        std::vector<WordId> h(history.begin()+(n-j-1), history.end()); // tmp history
        BaseNode* hnode = get_node(h);
        if (hnode)
        {
            int N1prx = get_N1prx(hnode, j);   // number of word types following the history
            if (!N1prx)  // break early, don't reset probabilities to 0
                break;   // for unknown histories

            // total number of occurences of the history
            int cs = sum_child_counts(hnode, j);
            if (cs)
            {
                // get ngram counts
                fill(vc.begin(), vc.end(), 0);
                int num_children = get_num_children(hnode, j);
                for(i=0; i<num_children; i++)
                {
                    BaseNode* child = get_child_at(hnode, j, i);
                    int index = binsearch(words, child->word_id); // word_indices have to be sorted by index
                    if (index >= 0)
                        vc[index] = child->get_count();
                }

                double lambda = N1prx / (N1prx + float(cs)); // normalization factor
                for(i=0; i<size; i++)
                {
                    double pmle = vc[i] / float(cs);
                    vp[i] = (1.0 - lambda) * pmle + lambda * vp[i];
                }
            }
        }
    }
}

// absolute discounting
template <class TNODE, class TBEFORELASTNODE, class TLASTNODE>
void NGramTrie<TNODE, TBEFORELASTNODE, TLASTNODE>::
     get_probs_abs_disc_i(const std::vector<WordId>& history,
                          const std::vector<WordId>& words,
                          std::vector<double>& vp,
                          int num_word_types,
                          const std::vector<double>& Ds)
{
    int i,j;
    int n = history.size() + 1;
    int size = words.size();   // number of candidate words
    std::vector<int32_t> vc(size);  // vector of counts, reused for order 1..n

    // order 0
    vp.resize(size);
    fill(vp.begin(), vp.end(), 1.0/num_word_types); // uniform distribution

    // order 1..n
    for(j=0; j<n; j++)
    {
        std::vector<WordId> h(history.begin()+(n-j-1), history.end()); // tmp history
        BaseNode* hnode = get_node(h);
        if (hnode)
        {
            int N1prx = get_N1prx(hnode, j);   // number of word types following the history
            if (!N1prx)  // break early, don't reset probabilities to 0
                break;   // for unknown histories

            // total number of occurences of the history
            int cs = sum_child_counts(hnode, j);
            if (cs)
            {
                // get ngram counts
                fill(vc.begin(), vc.end(), 0);
                int num_children = get_num_children(hnode, j);
                for(i=0; i<num_children; i++)
                {
                    BaseNode* child = get_child_at(hnode, j, i);
                    int index = binsearch(words, child->word_id); // word_indices have to be sorted by index
                    if (index >= 0)
                        vc[index] = child->get_count();
                }

                double D = Ds[j];
                double l1 = D / float(cs) * N1prx; // normalization factor
                                                   // 1 - lambda
                for(i=0; i<size; i++)
                {
                    double a = vc[i] - D;
                    if (a < 0)
                        a = 0;
                    vp[i] = a / float(cs) + l1 * vp[i];
                }
            }
        }
    }
}

//------------------------------------------------------------------------
// DynamicModel - dynamically updatable language model
//------------------------------------------------------------------------
template <class TNGRAMS>
void _DynamicModel<TNGRAMS>::set_order(int n)
{
    n1s = std::vector<int>(n, 0);
    n2s = std::vector<int>(n, 0);
    Ds  = std::vector<double>(n, 0);

    ngrams.set_order(n);
    NGramModel::set_order(n);  // calls clear()
}

template <class TNGRAMS>
void _DynamicModel<TNGRAMS>::clear()
{
    NGramModel::clear();  // clears dictionary

    ngrams.clear();

    // Add entries for control words.
    // Add them with a count of 1 as 0 throws off the normalization
    // of witten-bell smoothing.
    const wchar_t* words[] = {L"<unk>", L"<s>", L"</s>", L"<num>"};
    for (int i=0; i<ALEN(words); i++)
    {
        count_ngram(words+i, 1, 1);
        assert(dictionary.word_to_id(words[i]) == i);
    }
}

// Add increment to the count of the given ngram.
// Unknown words will be added to the dictionary and
// unknown ngrams will cause new trie nodes to be created as needed.
template <class TNGRAMS>
int _DynamicModel<TNGRAMS>::count_ngram(const wchar_t* const* ngram, int n,
                                      int increment, bool allow_new_words)
{
    int i;
    enum {ERR_NONE, ERR_MEMORY_DICT=-1};

    std::vector<WordId> wids(n);

    // get/add word
    for (i = 0; i < n; i++)
    {
        const wchar_t* word = ngram[i];

        WordId wid = dictionary.word_to_id(word);
        if (wid == WIDNONE)
        {
            if (allow_new_words)
            {
                wid = dictionary.add_word(word);
                if (wid == WIDNONE)
                    return ERR_MEMORY_DICT;
            }
            else
            {
                wid = UNKNOWN_WORD_ID;
            }
        }
        wids[i] = wid;
    }

    return count_ngram(&wids[0], n, increment);
}

// Add increment to the count of the given ngram.
// Unknown words will be added to the dictionary first and
// unknown ngrams will cause new trie nodes to be created as needed.
template <class TNGRAMS>
int _DynamicModel<TNGRAMS>::count_ngram(const WordId* wids, int n, int increment)
{
    int i;
    enum {ERR_NONE, ERR_MEMORY_NGRAM=-2, ERR_MEMORY_INC=-3}
    error = ERR_NONE;

    // get/add node for ngram
    BaseNode* node = ngrams.add_node(wids, n);
    if (!node)
        return ERR_MEMORY_NGRAM;

    if (increment_node_count(node, wids, n, increment) < 0)
        return ERR_MEMORY_INC;

    // add new state
    if (node->count == 1)
        n1s[n-1] += 1;
    if (node->count == 2)
        n2s[n-1] += 1;

    // estimate discounting parameters for absolute discounting, kneser-ney
    for (i = 0; i < order; i++)
    {
        double D;
        int n1 = n1s[i];
        int n2 = n2s[i];
        if (n1 == 0 || n2 == 0)
            D = 0.1;          // training corpus too small, fake a value
        else
            // deleted estimation, Ney, Essen, and Kneser 1994
            D = n1 / (n1 + 2.0*n2);
        ASSERT(0 <= D and D <= 1.0);
        //D = 0.1;
        Ds[i] = D;
    }

    return error;
}

// Return the number of occurences of the given ngram
template <class TNGRAMS>
int _DynamicModel<TNGRAMS>::get_ngram_count(const wchar_t* const* ngram, int n)
{
    BaseNode* node = get_ngram_node(ngram, n);
    return (node ? node->get_count() : 0);
}

// Calculate a vector of probabilities for the ngrams formed
// from history + word[i], for all i.
// input:  constant history and a vector of candidate words
// output: vector of probabilities, one value per candidate word
template <class TNGRAMS>
void _DynamicModel<TNGRAMS>::get_probs(const std::vector<WordId>& history,
                            const std::vector<WordId>& words,
                            std::vector<double>& probabilities)
{
    // pad/cut history so it's always of length order-1
    int n = std::min((int)history.size(), order-1);
    std::vector<WordId> h(order-1, UNKNOWN_WORD_ID);
    copy_backward(history.end()-n, history.end(), h.end());

    #ifndef NDEBUG
    for (int i=0; i<order; i++)
        printf("%d: n1=%8d n2=%8d D=%f\n", i, n1s[i], n2s[i], Ds[i]);
    #endif

    switch(smoothing)
    {
        case WITTEN_BELL_I:
            ngrams.get_probs_witten_bell_i(h, words, probabilities,
                                              get_num_word_types());
            break;

        case ABS_DISC_I:
            ngrams.get_probs_abs_disc_i(h, words, probabilities,
                                           get_num_word_types(), Ds);
            break;

         default:
            break;
    }
}

// Load from ARPA-like format, expects counts instead of log probabilities
// and no back-off values. N-grams don't have to be sorted alphabetically.
template <class TNGRAMS>
int _DynamicModel<TNGRAMS>::load_arpac(const char* filename)
{
    int i;
    int new_order = 0;
    int current_level = 0;
    std::vector<int> counts;

    enum {ERR_NONE, ERR_FILE, ERR_MEMORY, ERR_NUMTOKENS,
          ERR_ORDER, ERR_COUNT, ERR_END}
    error = ERR_NONE;

    enum {BEGIN, COUNTS, NGRAMS_HEAD, NGRAMS, DONE}
    state = BEGIN;

    clear();

    FILE* f = fopen(filename, "r,ccs=UTF-8");
    if (!f)
    {
        #ifndef NDEBUG
        printf( "Error opening %s\n", filename);
        #endif
        return ERR_FILE;
    }

    while(1)
    {
        // read line
        wchar_t buf[4096];
        if (fgetws(buf, ALEN(buf), f) == NULL)
            break;

        // chop line into tokens
        wchar_t *tstate;
        wchar_t* tokens[32] = {wcstok(buf, L" \n", &tstate)};
        for (i=0; tokens[i] && i < ALEN(tokens)-1; i++)
            tokens[i+1] = wcstok(NULL, L" \n", &tstate);
        int ntoks = i;

        if (ntoks)  // any tokens there?
        {
            // check for n-grams first, this is by far the most frequent case
            if (state == NGRAMS)
            {
                if (tokens[0][0] == L'\\')
                {
                    if (ngrams.get_num_ngrams(current_level-1) !=
                        counts[current_level-1])
                    {
                        error = ERR_COUNT; // count doesn't match number of unique ngrams
                        break;
                    }
                    state = NGRAMS_HEAD;
                }
                else
                {
                    int count = wcstol(tokens[0], NULL, 10);
                    if (ntoks < current_level+1)
                    {
                        error = ERR_NUMTOKENS; // too few tokens for current ngram level
                        break;
                    }
                    int err = count_ngram(tokens+1, current_level, count);
                    if (err)
                    {
                        error = ERR_MEMORY; // out of memory
                        break;
                    }

                    continue;
                }
            }
            else
            if (state == BEGIN)
            {
                if (wcsncmp(tokens[0], L"\\data\\", 6) == 0)
                {
                    state = COUNTS;
                }
            }
            else
            if (state == COUNTS)
            {
                if (wcsncmp(tokens[0], L"ngram", 5) == 0 && ntoks >= 2)
                {
                    int level;
                    int count;
                    if (swscanf(tokens[1], L"%d=%d", &level, &count) == 2)
                    {
                        new_order = std::max(new_order, level);
                        counts.resize(new_order);
                        counts[level-1] = count;
                    }
                }
                else
                {
                    // clear language model and set it up for the new order
                    set_order(new_order);
                    if (new_order)
                    {
                        dictionary.reserve_words(counts[0]);
                        ngrams.reserve_unigrams(counts[0]);
                    }
                    state = NGRAMS_HEAD;
                }
            }

            if (state == NGRAMS_HEAD)
            {
                if (swscanf(tokens[0], L"\\%d-grams", &current_level) == 1)
                {
                    if (current_level < 1 || current_level > new_order)
                    {
                        error = ERR_ORDER;
                        break;
                    }
                    state = NGRAMS;
                }
                else
                if (wcsncmp(tokens[0], L"\\end\\", 5) == 0)
                {
                    state = DONE;
                    break;
                }
            }
        }
    }

    // not made it until the end?
    if (state != DONE)
    {
        clear();
        if (!error)
            error = ERR_END;  // unexpected end of file
    }

    return error;
}

// Save to ARPA-like format, stores counts instead of log probabilities
// and no back-off values.
template <class TNGRAMS>
int _DynamicModel<TNGRAMS>::save_arpac(const char* filename)
{
    int i;

    FILE* f = fopen(filename, "w,ccs=UTF-8");
    if (!f)
    {
        #ifndef NDEBUG
        printf( "Error opening %s\n", filename);
        #endif
        return -1;
    }

    fwprintf(f, L"\n");
    fwprintf(f, L"\\data\\\n");

    for (i=0; i<order; i++)
        fwprintf(f, L"ngram %d=%d\n", i+1, ngrams.get_num_ngrams(i));

    for (i=0; i<order; i++)
    {
        fwprintf(f, L"\n");
        fwprintf(f, L"\\%d-grams:\n", i+1);

        std::vector<WordId> wids;
        for (typename TNGRAMS::iterator it = ngrams.begin(); *it; it++)
        {
            if (it.get_level() == i+1)
            {
                it.get_ngram(wids);
                fwprintf(f, L"%d", (*it)->get_count());
                for(int j=0; j<=i; j++)
                    fwprintf(f, L" %ls", id_to_word(wids[j]));
                fwprintf(f, L"\n");
            }
        }
    }

    fwprintf(f, L"\n");
    fwprintf(f, L"\\end\\\n");

    fclose(f);

    return 0;
}

// load from format with depth first ngram traversal
// not much faster than load_arpa and more unusual file format -> disabled
template <class TNGRAMS>
int _DynamicModel<TNGRAMS>::load_depth_first(const char* filename)
{
    int i;
    int new_order = 0;
    std::vector<int> counts;
    std::vector<WordId> wids;

    enum {ERR_NONE, ERR_FILE, ERR_MEMORY, ERR_NUMTOKENS,
          ERR_ORDER, ERR_COUNT, ERR_END}
    error = ERR_NONE;

    enum {BEGIN, COUNTS, NGRAMS_HEAD, NGRAMS, END, DONE}
    state = BEGIN;

    clear();

    FILE* f = fopen(filename, "r,ccs=UTF-8");
    if (!f)
    {
        #ifndef NDEBUG
        printf( "Error opening %s\n", filename);
        #endif
        return ERR_FILE;
    }

    while(1)
    {
        // read line
        wchar_t buf[2048];
        if (fgetws(buf, ALEN(buf), f) == NULL)
            break;

        // chop line into tokens
        wchar_t *tstate;
        wchar_t* tokens[32] = {wcstok(buf, L" \n", &tstate)};
        for (i=0; tokens[i] && i < ALEN(tokens)-1; i++)
            tokens[i+1] = wcstok(NULL, L" \n", &tstate);
        int ntoks = i;

        if (ntoks)  // any tokens there?
        {
            // check for n-grams first
            if (state == NGRAMS)
            {
                if (tokens[0][0] == L'\\')
                {
                    for (i=0; i<new_order;i++)
                        if (ngrams.get_num_ngrams(i) != counts[i])
                    {
                        error = ERR_COUNT; // count doesn't match number of
                                           // unique ngrams
                        break;
                    }
                    state = END;
                }
                else
                {
                    if (ntoks < 3)
                    {
                        error = ERR_NUMTOKENS; // too few tokens for current ngram level
                        break;
                    }
                    int level = wcstol(tokens[0], NULL, 10);
                    int count = wcstol(tokens[1], NULL, 10);
                    const wchar_t* word = tokens[2];

                    WordId wid = dictionary.word_to_id(word);
                    if (wid == WIDNONE)
                    {
                        wid = dictionary.add_word(word);
                        if (wid == WIDNONE)
                        {
                            error = ERR_MEMORY;
                            break;
                        }
                    }
                    wids[level-1] = wid;

                    int err = count_ngram(&wids[0], level, count);
                    if (err)
                    {
                        error = ERR_MEMORY; // out of memory
                        break;
                    }

                    continue;
                }
            }
            else
            if (state == BEGIN)
            {
                if (wcsncmp(tokens[0], L"\\data\\", 6) == 0)
                {
                    state = COUNTS;
                }
            }
            else
            if (state == COUNTS)
            {
                if (wcsncmp(tokens[0], L"ngram", 5) == 0 && ntoks >= 2)
                {
                    int level;
                    int count;
                    if (swscanf(tokens[1], L"%d=%d", &level, &count) == 2)
                    {
                        new_order = std::max(new_order, level);
                        counts.resize(new_order);
                        counts[level-1] = count;
                    }
                }
                else
                {
                    // clear language model and set it up for the new order
                    set_order(new_order);
                    if (new_order)
                    {
                        dictionary.reserve_words(counts[0]);
                        ngrams.reserve_unigrams(counts[0]);
                        wids = std::vector<WordId>(new_order, 0);
                    }
                    state = NGRAMS_HEAD;
                }
            }

            if (state == NGRAMS_HEAD)
            {
                if (wcsncmp(tokens[0], L"\\n-grams:", 9) == 0)
                {
                    state = NGRAMS;
                }
            }

            if (state == END)
            {
                if (wcsncmp(tokens[0], L"\\end\\", 5) == 0)
                {
                    state = DONE;
                    break;
                }
            }
        }
    }

    // not made it until the end?
    if (state != DONE)
    {
        clear();
        if (!error)
            error = ERR_END;  // unexpected end of file
    }

    return error;
}


// Save to format with depth first ngram traversal
template <class TNGRAMS>
int _DynamicModel<TNGRAMS>::save_depth_first(const char* filename)
{
    FILE* f = fopen(filename, "w,ccs=UTF-8");
    if (!f)
    {
        #ifndef NDEBUG
        printf( "Error opening %s\n", filename);
        #endif
        return -1;
    }

    fwprintf(f, L"\n");
    fwprintf(f, L"\\data\\\n");

    for (int i=0; i<order; i++)
        fwprintf(f, L"ngram %d=%d\n", i+1, ngrams.get_num_ngrams(i));

    fwprintf(f, L"\n");
    fwprintf(f, L"\\n-grams:\n");

    typename TNGRAMS::iterator it;
    for (it = ngrams.begin(); *it; it++)
    {
        int level = it.get_level();
        fwprintf(f, L"%d %d %ls\n", level,
                                       (*it)->get_count(),
                                       id_to_word((*it)->word_id));
    }

    fwprintf(f, L"\n");
    fwprintf(f, L"\\end\\\n");

    fclose(f);

    return 0;
}
