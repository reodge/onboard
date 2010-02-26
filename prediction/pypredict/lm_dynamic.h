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

#ifndef LM_DYNAMIC_H
#define LM_DYNAMIC_H

#include <math.h>
#include <cstring>   // memcpy
#include "lm.h"

#pragma pack(2)

//------------------------------------------------------------------------
// inplace_vector - expects its elements in anonymous memory right after itself
//------------------------------------------------------------------------

template <class T>
class inplace_vector
{
    public:
        inplace_vector()
        {
            num_items = 0;
        }

        int capacity()
        {
            return capacity(num_items);
        }

        static int capacity(int n)
        {
            if (n == 0)
                n = 1;

            // growth factor, lower for slower growth and less wasted memory
            // g=2.0: quadratic growth, double capacity per step
            // [int(1.25**math.ceil(math.log(x)/math.log(1.25))) for x in range (1,100)]
            double g = 1.25;
            return (int) pow(g,ceil(log(n)/log(g)));
        }

        int size()
        {
            return num_items;
        }

        T* buffer()
        {
            return (T*) (((uint8_t*)(this) + sizeof(inplace_vector<T>)));
        }

        T& operator [](int index)
        {
            ASSERT(index >= 0 && index <= capacity());
            return buffer()[index];
        }

        T& back()
        {
            ASSERT(size() > 0);
            return buffer()[size()-1];
        }

        void push_back(T& item)
        {
            buffer()[size()] = item;
            num_items++;
            ASSERT(size() <= capacity());
        }

        void insert(int index, T& item)
        {
            T* p = buffer();
            for (int i=size()-1; i>=index; --i)
                p[i+1] = p[i];
            p[index] = item;
            num_items++;
            ASSERT(size() <= capacity());
        }

    public:
        uint16_t num_items;
};


//------------------------------------------------------------------------
// BaseNode - base class of all trie nodes
//------------------------------------------------------------------------

class BaseNode
{
    public:
        BaseNode(WordId wid = -1)
        {
            word_id = wid;
            count = 0;
        }

        void clear()
        {
            count = 0;
        }

        const int get_count() const
        {
            return count;
        }

        void set_count(int c)
        {
            count = c;
        }


    public:
        WordId word_id;
        uint32_t count;
};

//------------------------------------------------------------------------
// LastNode - leaf node of the ngram trie, trigram for order 3
//------------------------------------------------------------------------
template <class TBASE>
class LastNode : public TBASE
{
    public:
        LastNode(WordId wid = (WordId)-1)
        : TBASE(wid)
        {
        }
};

//------------------------------------------------------------------------
// BeforeLastNode - second to last node of the ngram trie, bigram for order 3
//------------------------------------------------------------------------
template <class TBASE, class TLASTNODE>
class BeforeLastNode : public TBASE
{
    public:
        BeforeLastNode(WordId wid = (WordId)-1)
        : TBASE(wid)
        {
        }

        TLASTNODE* add_child(WordId wid)
        {
            TLASTNODE node(wid);
            if (children.size())
            {
                int index = search_index(wid);
                children.insert(index, node);
                //printf("insert: index=%d wid=%d\n",index, wid);
                return &children[index];
            }
            else
            {
                children.push_back(node);
                //printf("push_back: size=%d wid=%d\n",(int)children.size(), wid);
                return &children.back();
            }
        }

        BaseNode* get_child(WordId wid)
        {
            if (children.size())
            {
                int index = search_index(wid);
                if (index < (int)children.size())
                    if ((int)children[index].word_id == wid)
                        return &children[index];
            }
            return NULL;
        }

        BaseNode* get_child_at(int index)
        {
            return &children[index];
        }

        int search_index(WordId wid)
        {
            int lo = 0;
            int hi = children.size();
            while (lo < hi)
            {
                int mid = (lo+hi)>>1;
                if ((int)children[mid].word_id < wid)
                    lo = mid + 1;
                else
                    hi = mid;
            }
            return lo;
        }

        int get_N1prx() {return children.size();}  // assumes all have counts>=1

        int sum_child_counts()
        {
            int sum = 0;
            for (int i=0; i<children.size(); i++)
                sum += children[i].get_count();
            return sum;
        }
    public:
        inplace_vector<TLASTNODE> children;  // has to be last
};

//------------------------------------------------------------------------
// TrieNode - node for all lower levels of the ngram trie, unigrams for order 3
//------------------------------------------------------------------------
template <class TBASE>
class TrieNode : public TBASE
{
    public:
        TrieNode(WordId wid = (WordId)-1)
        : TBASE(wid)
        {
        }

        void add_child(BaseNode* node)
        {
            if (children.size())
            {
                int index = search_index(node->word_id);
                children.insert(children.begin()+index, node);
                //printf("insert: index=%d wid=%d\n",index, wid);
            }
            else
            {
                children.push_back(node);
                //printf("push_back: size=%d wid=%d\n",(int)children.size(), wid);
            }
        }

        BaseNode* get_child(WordId wid, int& index)
        {
            if (children.size())
            {
                index = search_index(wid);
                if (index < (int)children.size())
                    if ((int)children[index]->word_id == wid)
                        return children[index];
            }
            return NULL;
        }

        BaseNode* get_child_at(int index)
        {
            return children[index];
        }

        int search_index(WordId wid)
        {
            // binary search like lower_bound()
            int lo = 0;
            int hi = children.size();
            while (lo < hi)
            {
                int mid = (lo+hi)>>1;
                if ((int)children[mid]->word_id < wid)
                    lo = mid + 1;
                else
                    hi = mid;
            }
            return lo;
        }

        int get_N1prx()
        {
            int n = children.size();  // assumes all children have counts > 0

            // Unigrams <unk>, <s>,... may be empty initially. Don't count them
            // or predictions for small models won't sum close to 1.0
            for (int i=0; i<n && i<LanguageModel::NUM_CONTROL_WORDS; i++)
                if (children[0]->get_count() == 0)
                    n--;
            return n;
        }

        int sum_child_counts()
        {
            int sum = 0;
            std::vector<BaseNode*>::iterator it;
            for (it=children.begin(); it!=children.end(); it++)
                sum += (*it)->get_count();
            return sum;
        }
    public:
        std::vector<BaseNode*> children;
};

//------------------------------------------------------------------------
// NGramTrie - root node of the ngram trie
//------------------------------------------------------------------------
template <class TNODE, class TBEFORELASTNODE, class TLASTNODE>
class NGramTrie : public TNODE
{
    public:
        class iterator
        {
            public:
                iterator()
                {
                    root = NULL;
                }
                iterator(NGramTrie* root)
                {
                    this->root = root;
                    nodes.push_back(root);
                    indexes.push_back(0);
                    operator++(0);
                }

                BaseNode* operator*() const // dereference operator
                {
                    if (nodes.empty())
                        return NULL;
                    else
                        return nodes.back();
                }

                void operator++(int unused) // postfix operator
                {
                    // preorder traversal with shallow stack
                    // nodes stack: path to node
                    // indexes stack: index of _next_ child
                    BaseNode* node = nodes.back();
                    int index = indexes.back();

                    int level = get_level();
                    int size = root->get_num_children(node, level);
                    while (index >= root->get_num_children(node, level))
                    {
                        nodes.pop_back();
                        indexes.pop_back();
                        if (nodes.empty())
                            return;

                        node = nodes.back();
                        index = ++indexes.back();
                        level = nodes.size()-1;
                        size = root->get_num_children(node, level);
                        //printf ("back %d %d\n", node->word_id, index);
                    }
                    node = root->get_child_at(node, level, index);
                    nodes.push_back(node);
                    indexes.push_back(0);
                    //printf ("pushed %d %d %d\n", nodes.back()->word_id, index, indexes.back());
                }

                void get_ngram(std::vector<WordId>& ngram)
                {
                    ngram.resize(nodes.size()-1);
                    for(int i=1; i<(int)nodes.size(); i++)
                        ngram[i-1] = nodes[i]->word_id;
                }

                int get_level()
                {
                    return nodes.size()-1;
                }

                int at_root()
                {
                    return get_level() == 0;
                }

            private:
                NGramTrie<TNODE, TBEFORELASTNODE, TLASTNODE>* root;
                std::vector<BaseNode*> nodes;   // path to node
                std::vector<int> indexes;       // index of _next_ child
        };

        NGramTrie::iterator begin()
        {
            return NGramTrie::iterator(this);
        }


    public:
        NGramTrie(WordId wid = (WordId)-1)
        : TNODE(wid)
        {
            order = 0;
        }

        void set_order(int order)
        {
            this->order = order;
            clear();
        }

        void clear()
        {
            clear(this, 0);
            num_ngrams   = std::vector<int>(order, 0);
            total_ngrams = std::vector<int>(order, 0);
            TNODE::clear();
        }

        // Add increment to node->count
        int increment_node_count(BaseNode* node, const WordId* wids, int n,
                                 int increment)
        {
            total_ngrams[n-1] += increment;
            node->count += increment;
            return node->count;
        }

        BaseNode* add_node(const std::vector<WordId>& wids)
        {return add_node(&wids[0], wids.size());}
        BaseNode* add_node(const WordId* wids, int n);

        void get_probs_witten_bell_i(const std::vector<WordId>& history,
                                     const std::vector<WordId>& words,
                                     std::vector<double>& vp,
                                     int num_word_types);

        void get_probs_abs_disc_i(const std::vector<WordId>& history,
                                  const std::vector<WordId>& words,
                                  std::vector<double>& vp,
                                  int num_word_types,
                                  const std::vector<double>& Ds);

        // get number of unique ngrams
        int get_num_ngrams(int level) { return num_ngrams[level]; }

        // get total number of all ngram occurences
        int get_total_ngrams(int level) { return total_ngrams[level]; }

        // get number of occurences of a specific ngram
        int get_ngram_count(const std::vector<WordId>& wids)
        {
            BaseNode* node = get_node(wids);
            if (node)
                return node->get_count();
            return 0;
        }

        BaseNode* get_node(const std::vector<WordId>& wids)
        {
            BaseNode* node = this;
            for (int i=0; i<(int)wids.size(); i++)
            {
                int index;
                node = get_child(node, i, wids[i], index);
                if (!node)
                    break;
            }
            return node;
        }

        int get_num_children(BaseNode* node, int level)
        {
            if (level == order)
                return 0;
            if (level == order - 1)
                return static_cast<TBEFORELASTNODE*>(node)->children.size();
            return static_cast<TNODE*>(node)->children.size();
        }

        int sum_child_counts(BaseNode* node, int level)
        {
            if (level == order)
                return -1;  // undefined for leaf nodes
            if (level == order - 1)
                return static_cast<TBEFORELASTNODE*>(node)->sum_child_counts();
            return static_cast<TNODE*>(node)->sum_child_counts();
        }

        BaseNode* get_child_at(BaseNode* parent, int level, int index)
        {
            if (level == order)
                return NULL;
            if (level == order - 1)
                return &static_cast<TBEFORELASTNODE*>(parent)->children[index];
            return static_cast<TNODE*>(parent)->children[index];
        }

        int get_N1prx(BaseNode* node, int level)
        {
            if (level == order)
                return 0;
            if (level == order - 1)
                return static_cast<TBEFORELASTNODE*>(node)->get_N1prx();
            return static_cast<TNODE*>(node)->get_N1prx();
        }

        // -------------------------------------------------------------------
        // implementation specific
        // -------------------------------------------------------------------

        // reserve an exact number of items to avoid unessarily
        // overallocated memory when loading language models
        void reserve_unigrams(int count)
        {
            clear();
            TNODE::children.reserve(count);
        }


        // Estimate a lower bound for the memory usage of the whole trie.
        // This includes overallocations by std::vector, but excludes memory
        // used for heap management and possible heap fragmentation.
        uint64_t get_memory_size()
        {
            NGramTrie::iterator it = begin();
            uint64_t sum = 0;
            for (; *it; it++)
                sum += get_node_memory_size(*it, it.get_level());
            return sum;
        }


    protected:
        void clear(BaseNode* node, int level)
        {
            if (level < order-1)
            {
                TNODE* tn = static_cast<TNODE*>(node);
                std::vector<BaseNode*>::iterator it;
                for (it=tn->children.begin(); it<tn->children.end(); it++)
                {
                    clear(*it, level+1);
                    if (level < order-2)
                        static_cast<TNODE*>(*it)->~TNODE();
                    else
                    if (level < order-1)
                        static_cast<TBEFORELASTNODE*>(*it)->~TBEFORELASTNODE();
                    MemFree(*it);

                }
                std::vector<BaseNode*>().swap(tn->children);  // really free the memory
            }
            TNODE::set_count(0);
        }


        BaseNode* get_child(BaseNode* parent, int level, int wid, int& index)
        {
            if (level == order)
                return NULL;
            if (level == order - 1)
                return static_cast<TBEFORELASTNODE*>(parent)->get_child(wid);
            return static_cast<TNODE*>(parent)->get_child(wid, index);
        }

        int get_node_memory_size(BaseNode* node, int level)
        {
            if (level == order)
                return sizeof(TLASTNODE);
            if (level == order - 1)
            {
                TBEFORELASTNODE* nd = static_cast<TBEFORELASTNODE*>(node);
                return sizeof(TBEFORELASTNODE) +
                       sizeof(TLASTNODE) *
                       (nd->children.capacity() - nd->children.size());
            }

            TNODE* nd = static_cast<TNODE*>(node);
            return sizeof(TNODE) +
                   sizeof(TNODE*) * nd->children.capacity();
        }


    public:
        int order;
        std::vector<int> num_ngrams;
        std::vector<int> total_ngrams;
};

#pragma pack()


enum Smoothing
{
    SMOOTHING_NONE,
    JELINEK_MERCER_I,    // jelinek-mercer interpolated
    WITTEN_BELL_I,       // witten-bell interpolated
    ABS_DISC_I,          // absolute discounting interpolated
    KNESER_NEY_I,        // kneser-ney interpolated
};

//------------------------------------------------------------------------
// DynamicModelBase - non-template abstract base class of all DynamicModels
//------------------------------------------------------------------------
class DynamicModelBase : public NGramModel
{
    public:
        // iterator for template-free, polymorphy based ngram traversel
        class ngrams_iter
        {
            public:
                virtual BaseNode* operator*() const = 0;
                virtual void operator++(int unused) = 0;
                virtual void get_ngram(std::vector<WordId>& ngram) = 0;
                virtual bool at_root() = 0;
        };
        virtual DynamicModelBase::ngrams_iter* ngrams_begin() = 0;
        virtual void get_node_values(BaseNode* node, int level,
                                     std::vector<int>& values) = 0;

    public:
        virtual void get_candidates(const wchar_t*prefix,
                                 std::vector<WordId>& wids,
                                 bool filter_control_words=true);
    protected:
        virtual void set_node_time(BaseNode* node, uint32_t time)
        {}    
};

//------------------------------------------------------------------------
// DynamicModel - dynamically updatable language model
//------------------------------------------------------------------------
template <class TNGRAMS>
class _DynamicModel : public DynamicModelBase
{
    public:
        static const Smoothing DEFAULT_SMOOTHING = ABS_DISC_I;

        class ngrams_iter : public DynamicModelBase::ngrams_iter
        {
            public:
                ngrams_iter(_DynamicModel<TNGRAMS>* lm)
                : it(&lm->ngrams)
                {}

                virtual BaseNode* operator*() const // dereference operator
                { return *it; }

                virtual void operator++(int unused) // postfix operator
                { it++; }

                virtual void get_ngram(std::vector<WordId>& ngram)
                { it.get_ngram(ngram); }

                virtual bool at_root()
                { return it.at_root(); }

            public:
                typename TNGRAMS::iterator it;
        };
        virtual DynamicModelBase::ngrams_iter* ngrams_begin()
        {return new ngrams_iter(this);}

    public:
        _DynamicModel()
        {
            smoothing = DEFAULT_SMOOTHING;
            set_order(3);
        }

        virtual ~_DynamicModel()
        {
            #ifndef NDEBUG
            uint64_t v = dictionary.get_memory_size();
            uint64_t n = ngrams.get_memory_size();
            printf("memory: dictionary=%ld, ngrams=%ld, total=%ld\n", v, n, v+n);
            #endif

            clear();
        }

        virtual void clear();
        virtual void set_order(int order);
        virtual Smoothing get_smoothing() {return smoothing;}
        virtual void set_smoothing(Smoothing s) {smoothing = s;}

        virtual std::vector<Smoothing> get_smoothings()
        {
            std::vector<Smoothing> smoothings;
            smoothings.push_back(WITTEN_BELL_I);
            smoothings.push_back(ABS_DISC_I);
            return smoothings;
        }

        virtual BaseNode* count_ngram(const wchar_t* const* ngram, int n,
                                int increment=1, bool allow_new_words=true);
        virtual BaseNode* count_ngram(const WordId* wids, int n, int increment);
        virtual int get_ngram_count(const wchar_t* const* ngram, int n);

        virtual LanguageModel::Error load(const char* filename)
        {return load_arpac(filename);}
        virtual LanguageModel::Error save(const char* filename)
        {return save_arpac(filename);}

        virtual void get_node_values(BaseNode* node, int level,
                                     std::vector<int>& values)
        {
            values.push_back(node->count);
            values.push_back(ngrams.get_N1prx(node, level));
        }
        virtual void get_memory_sizes(std::vector<long>& values)
        {
            values.push_back(dictionary.get_memory_size());
            values.push_back(ngrams.get_memory_size());
        }

    protected:
        virtual LanguageModel::Error write_arpa_ngram(FILE* f, const BaseNode* node,
                                       const std::vector<WordId>& wids);

        virtual LanguageModel::Error load_arpac(const char* filename);
        virtual LanguageModel::Error save_arpac(const char* filename);
        virtual LanguageModel::Error load_depth_first(const char* filename);
        virtual LanguageModel::Error save_depth_first(const char* filename);

        virtual void get_probs(const std::vector<WordId>& history,
                                    const std::vector<WordId>& words,
                                    std::vector<double>& probabilities);

        virtual int increment_node_count(BaseNode* node, const WordId* wids,
                                         int n, int increment)
        {
            return ngrams.increment_node_count(node, wids, n, increment);
        }

   private:
        BaseNode* get_ngram_node(const wchar_t* const* ngram, int n)
        {
            std::vector<WordId> wids(n);
            for (int i=0; i<n; i++)
                wids[i] = dictionary.word_to_id(ngram[i]);
            return ngrams.get_node(wids);
        }

    protected:
        TNGRAMS ngrams;
        Smoothing smoothing;
        std::vector<int> n1s;
        std::vector<int> n2s;
        std::vector<double> Ds;
};

typedef _DynamicModel<NGramTrie<TrieNode<BaseNode>,
                                BeforeLastNode<BaseNode, LastNode<BaseNode> >,
                                LastNode<BaseNode> > > DynamicModel;

#include "lm_dynamic_impl.h"

#endif
